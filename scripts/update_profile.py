#!/usr/bin/env python3
"""仅使用公开 GitHub 数据更新主页；--check 不写入，--offline SNAPSHOT 无网络重渲染。"""

import argparse
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_BASE = "https://api.github.com"
GITHUB_BASE = "https://github.com"
MARKERS = ("CONTRIBUTIONS", "PROJECT-STARS")
LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
REPOSITORY = rf"{LOGIN}/[A-Za-z0-9_.-]+"


class ProfileError(Exception):
    """可安全展示的错误，不包含响应正文或凭据。"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    def __init__(self, token):
        self._token = token
        self._opener = build_opener(NoRedirect())

    def get(self, path, params=None):
        if path != "/search/issues":
            if not path.startswith("/repos/"):
                raise ProfileError("拒绝非白名单 GitHub API 路径。")
            repository(path.removeprefix("/repos/"))
        url = API_BASE + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={
            "Authorization": "Bearer " + self._token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kernelvale-profile-updater",
        }, method="GET")
        waited = 0
        for attempt in range(3):
            delay = 2 ** attempt
            try:
                with self._opener.open(request, timeout=30) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise ProfileError("GitHub API 返回了非对象数据。")
                return payload
            except HTTPError as error:
                status = error.code
                headers = {key.lower(): value for key, value in (error.headers or {}).items()}
                error.close()
                limited = status == 429 or (status == 403 and (
                    "retry-after" in headers or headers.get("x-ratelimit-remaining", "").strip() == "0"
                ))
                if (not limited and status not in {500, 502, 503, 504}) or attempt == 2:
                    raise ProfileError(f"GitHub API 请求失败（HTTP {status}）。") from None
                if limited:
                    delay = rate_limit_delay(headers)
            except (URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise ProfileError("GitHub API 网络请求失败，已达到重试上限。") from None
            except (ValueError, UnicodeError):
                raise ProfileError("GitHub API 返回了无效 JSON。") from None
            # 不截短服务端指定的等待时间；预算不足直接失败，避免提前重试。
            if delay > 30 - waited:
                raise ProfileError("GitHub API 重试等待超过 30 秒预算，保留旧文件。") from None
            time.sleep(delay)
            waited += delay
        raise ProfileError("GitHub API 请求失败。")


def rate_limit_delay(headers):
    now = time.time()
    delays = []
    try:
        if "retry-after" in headers:
            value = headers["retry-after"].strip()
            if re.fullmatch(r"[0-9]+", value):
                delays.append(int(value))
            else:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                delays.append(max(0, stamp.timestamp() - now))
        if "x-ratelimit-reset" in headers and (
            "retry-after" not in headers or headers.get("x-ratelimit-remaining", "").strip() == "0"
        ):
            value = headers["x-ratelimit-reset"].strip()
            if not re.fullmatch(r"[0-9]+", value):
                raise ValueError
            delays.append(max(0, int(value) - now))
    except (ValueError, TypeError, OverflowError):
        raise ProfileError("GitHub API 限流等待响应头无效，保留旧文件。") from None
    # 无有效等待指示时，按 GitHub 建议至少等待 60 秒，而不是短暂退避。
    return max(delays) if delays else 60


def integer(value, name):
    if type(value) is not int or value < 0:
        raise ProfileError(f"{name} 必须为非负整数。")
    return value


def repository(value):
    if (not isinstance(value, str) or not re.fullmatch(REPOSITORY, value)
            or value.split("/")[1] in {".", ".."}):
        raise ProfileError("仓库全名无效。")
    return value


def text(value):
    if not isinstance(value, str):
        raise ProfileError("公开文本字段无效。")
    return value


def merged_time(value):
    try:
        stamp = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ProfileError("PR 合并时间无效。") from None


def load_config(raw):
    if not isinstance(raw, dict) or not re.fullmatch(LOGIN, text(raw.get("username"))):
        raise ProfileError("配置中的 username 无效。")
    notes = raw.get("contribution_notes", {})
    featured = raw.get("featured_repositories", [])
    if not isinstance(notes, dict) or not isinstance(featured, list):
        raise ProfileError("配置中的仓库列表或贡献说明无效。")
    notes = {repository(key).casefold(): text(value) for key, value in notes.items()}
    featured = [repository(repo) for repo in featured]
    if len({repo.casefold() for repo in featured}) != len(featured):
        raise ProfileError("精选仓库配置重复。")
    return {
        "username": raw["username"],
        "contribution_notes": notes,
        "featured_repositories": featured,
        "max_contribution_repos": integer(raw.get("max_contribution_repos", 8), "仓库展示上限"),
        "max_recent_prs": integer(raw.get("max_recent_prs", 4), "最近 PR 展示上限"),
    }


def search_merged_prs(api, username):
    results = []
    total = None
    page = 1
    while total is None or len(results) < total:
        payload = api.get("/search/issues", {
            "q": f"author:{username} is:pr is:public is:merged",
            "per_page": 100, "page": page, "sort": "updated", "order": "desc",
        })
        if payload.get("incomplete_results") is not False:
            raise ProfileError("GitHub 搜索结果不完整，保留旧文件。")
        count = integer(payload.get("total_count"), "搜索结果数")
        if count > 1000:
            raise ProfileError("GitHub 搜索结果超过 1000 条，保留旧文件。")
        if total is not None and total != count:
            raise ProfileError("分页期间搜索结果数发生变化，请稍后重试。")
        total = count
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != min(100, total - len(results)):
            raise ProfileError("GitHub 搜索分页缺失或数量不一致。")
        if not all(isinstance(item, dict) for item in items):
            raise ProfileError("GitHub 搜索条目无效。")
        results.extend(items)
        page += 1
    return results


def get_repository(api, name):
    metadata = api.get("/repos/" + repository(name))
    full_name = repository(metadata.get("full_name"))
    owner = metadata.get("owner")
    if (full_name.casefold() != name.casefold() or not isinstance(owner, dict)
            or text(owner.get("login")).casefold() != full_name.split("/")[0].casefold()):
        raise ProfileError("GitHub 仓库元信息不一致。")
    if type(metadata.get("private")) is not bool or type(metadata.get("fork")) is not bool:
        raise ProfileError("GitHub 仓库公开性或 fork 信息缺失。")
    visibility = metadata.get("visibility")
    if visibility is not None and visibility not in {"public", "private", "internal"}:
        raise ProfileError("GitHub 仓库可见性无效。")
    public = metadata["private"] is False and visibility in {None, "public"}
    language = metadata.get("language")
    if language is not None:
        language = text(language)
    return {
        "repo": full_name,
        "public": public,
        "fork": metadata["fork"],
        "stars": integer(metadata.get("stargazers_count"), "Stars"),
        "language": language,
    }


def sort_prs(prs):
    return sorted(prs, key=lambda pr: (pr["merged_at"], pr["number"]), reverse=True)


def collect_snapshot(api, config, updated_at):
    username = config["username"]
    grouped = {}
    seen = set()
    for item in search_merged_prs(api, username):
        match = re.fullmatch(rf"{re.escape(API_BASE)}/repos/({REPOSITORY})", text(item.get("repository_url")))
        if not match:
            raise ProfileError("搜索结果中的仓库地址无效。")
        name = repository(match.group(1))
        user = item.get("user")
        pull = item.get("pull_request")
        if (not isinstance(user, dict) or text(user.get("login")).casefold() != username.casefold()
                or not isinstance(pull, dict) or item.get("state") != "closed"):
            raise ProfileError("搜索结果不是指定用户的已合并 PR。")
        number = integer(item.get("number"), "PR 编号")
        if number == 0 or (name.casefold(), number) in seen:
            raise ProfileError("搜索结果中的 PR 编号无效或重复。")
        seen.add((name.casefold(), number))
        pr = {"number": number, "title": text(item.get("title")),
              "merged_at": merged_time(pull.get("merged_at"))}
        if name.split("/")[0].casefold() != username.casefold():
            grouped.setdefault(name.casefold(), {"name": name, "prs": []})["prs"].append(pr)

    cache = {}

    def metadata(name):
        key = name.casefold()
        if key not in cache:
            cache[key] = get_repository(api, name)
        return cache[key]

    contributions = []
    for key in sorted(grouped):
        group = grouped[key]
        repo = metadata(group["name"])
        if (not repo["public"] or repo["fork"]
                or repo["repo"].split("/")[0].casefold() == username.casefold()):
            continue
        prs = sort_prs(group["prs"])
        contributions.append({
            "repo": repo["repo"], "stars": repo["stars"], "prs": prs,
            "note": config["contribution_notes"].get(key) or prs[0]["title"],
        })
    contributions.sort(key=lambda row: (-len(row["prs"]), row["repo"].casefold()))
    featured = []
    for name in config["featured_repositories"]:
        repo = metadata(name)
        if not repo["public"]:
            raise ProfileError("精选仓库不是公开仓库，保留旧文件。")
        featured.append({"repo": repo["repo"], "stars": repo["stars"], "language": repo["language"]})
    return {"username": username, "updated_at": updated_at,
            "contributions": contributions, "featured": featured}


def validate_snapshot(raw, config):
    """离线输入也只保留白名单字段，不传播额外数据或不可信链接。"""
    if not isinstance(raw, dict) or raw.get("username") != config["username"]:
        raise ProfileError("快照用户名与配置不一致。")
    try:
        updated_at = text(raw.get("updated_at"))
        if date.fromisoformat(updated_at).isoformat() != updated_at:
            raise ValueError
    except ValueError:
        raise ProfileError("快照日期无效。") from None
    if not isinstance(raw.get("contributions"), list) or not isinstance(raw.get("featured"), list):
        raise ProfileError("快照仓库列表无效。")
    contributions = []
    seen = set()
    for row in raw["contributions"]:
        if not isinstance(row, dict):
            raise ProfileError("快照贡献条目无效。")
        name = repository(row.get("repo"))
        key = name.casefold()
        if key in seen or name.split("/")[0].casefold() == config["username"].casefold():
            raise ProfileError("快照包含重复仓库或本人仓库。")
        seen.add(key)
        if not isinstance(row.get("prs"), list) or not row["prs"]:
            raise ProfileError("快照已合并 PR 列表无效。")
        prs = []
        numbers = set()
        for pr in row["prs"]:
            if not isinstance(pr, dict):
                raise ProfileError("快照 PR 条目无效。")
            number = integer(pr.get("number"), "PR 编号")
            if number == 0 or number in numbers:
                raise ProfileError("快照 PR 编号无效或重复。")
            numbers.add(number)
            prs.append({"number": number, "title": text(pr.get("title")),
                        "merged_at": merged_time(pr.get("merged_at"))})
        contributions.append({"repo": name, "stars": integer(row.get("stars"), "Stars"),
                              "prs": sort_prs(prs), "note": text(row.get("note"))})
    contributions.sort(key=lambda row: (-len(row["prs"]), row["repo"].casefold()))
    featured_by_name = {}
    for row in raw["featured"]:
        if not isinstance(row, dict):
            raise ProfileError("快照精选条目无效。")
        name = repository(row.get("repo"))
        if name.casefold() in featured_by_name:
            raise ProfileError("快照精选仓库重复。")
        language = row.get("language")
        featured_by_name[name.casefold()] = {
            "repo": name, "stars": integer(row.get("stars"), "Stars"),
            "language": None if language is None else text(language),
        }
    expected = [name.casefold() for name in config["featured_repositories"]]
    if set(featured_by_name) != set(expected):
        raise ProfileError("快照精选仓库与配置不一致。")
    return {"username": config["username"], "updated_at": updated_at,
            "contributions": contributions,
            "featured": [featured_by_name[key] for key in expected]}


def markdown(value):
    value = re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", " ".join(value.split()))
    return html.escape(value, quote=True)


def repo_link(name, short=False):
    label = name.split("/")[1] if short else name
    return f"[{markdown(label)}]({GITHUB_BASE}/{name})"


def pr_link(name, pr, title=False):
    label = f"{name}#{pr['number']} · {pr['title']}" if title else f"#{pr['number']}"
    return f"[{markdown(label)}]({GITHUB_BASE}/{name}/pull/{pr['number']})"


def render_regions(snapshot, config):
    lines = ["| 公共上游仓库 | 我的贡献 | 已合并 PR | 最近 PR | 🌟 仓库 Stars |",
             "| --- | --- | ---: | --- | ---: |"]
    for row in snapshot["contributions"][:config["max_contribution_repos"]]:
        links = "、".join(pr_link(row["repo"], pr) for pr in row["prs"][:2])
        lines.append(f"| {repo_link(row['repo'])} | {markdown(row['note'])} | "
                     f"{len(row['prs'])} | {links} | {row['stars']} |")
    recent = [(row["repo"], pr) for row in snapshot["contributions"] for pr in row["prs"]]
    recent.sort(key=lambda pair: (pair[0].casefold(), -pair[1]["number"]))
    recent.sort(key=lambda pair: pair[1]["merged_at"], reverse=True)
    if recent and config["max_recent_prs"]:
        lines.extend(["", "最近合并的公开 PR：", ""])
        lines.extend("- " + pr_link(name, pr, title=True)
                     for name, pr in recent[:config["max_recent_prs"]])
    lines.extend(["", "Stars 为仓库指标，非个人获得。",
                  f"数据日期（UTC）：{snapshot['updated_at']}。"])
    projects = ["| 项目 | 🌟 Stars | 主要语言 |", "| --- | ---: | --- |"]
    for row in snapshot["featured"]:
        projects.append(f"| {repo_link(row['repo'], short=True)} | {row['stars']} | "
                        f"{markdown(row['language'] or '—')} |")
    return {"CONTRIBUTIONS": "\n".join(lines), "PROJECT-STARS": "\n".join(projects)}


def marker_ranges(readme):
    ranges = []
    for name in MARKERS:
        start, end = f"<!-- {name}:START -->", f"<!-- {name}:END -->"
        if readme.count(start) != 1 or readme.count(end) != 1:
            raise ProfileError(f"README 的 {name} marker 必须唯一且成对出现。")
        left, right = readme.index(start), readme.index(end)
        if left >= right:
            raise ProfileError(f"README 的 {name} marker 顺序错误。")
        ranges.append((left, right + len(end), left + len(start), right, name))
    ranges.sort()
    if ranges[0][1] > ranges[1][0]:
        raise ProfileError("README marker 区域不能嵌套或交叉。")
    return ranges


def replace_regions(readme, regions):
    for _, _, left, right, name in reversed(marker_ranges(readme)):
        readme = readme[:left] + "\n" + regions[name].strip("\n") + "\n" + readme[right:]
    return readme


def metrics(snapshot):
    rows = snapshot["contributions"]
    return (len(rows), sum(len(row["prs"]) for row in rows),
            sum(row["stars"] for row in rows), sum(row["stars"] for row in snapshot["featured"]))


def render_svg(snapshot, dark=False):
    background, foreground, muted, border = (
        ("#0d1117", "#f0f6fc", "#a5b1bf", "#30363d") if dark else
        ("#ffffff", "#172b4d", "#52647a", "#dce3eb")
    )
    labels = ("外部贡献仓库数", "已合并外部PR数", "贡献仓库Stars合计", "精选项目Stars合计")
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="150" '
        'viewBox="0 0 1200 150" role="img" aria-labelledby="title desc">',
        '<title id="title">公开开源贡献统计</title>',
        '<desc id="desc">Stars 为仓库指标，非个人获得。</desc>',
        f'<rect width="1200" height="150" rx="16" fill="{background}"/>',
        '<g font-family="system-ui, sans-serif" text-anchor="middle">',
    ]
    for index, (label, value) in enumerate(zip(labels, metrics(snapshot))):
        x = 150 + 300 * index
        if index:
            parts.append(f'<path d="M{300 * index} 24v80" stroke="{border}"/>')
        parts.extend([
            f'<text x="{x}" y="40" font-size="16" fill="{muted}">{label}</text>',
            f'<text x="{x}" y="91" font-size="36" font-weight="700" fill="{foreground}">{value}</text>',
        ])
    footer = html.escape(f"Stars 为仓库指标，非个人获得 · 数据日期：{snapshot['updated_at']} UTC")
    parts.extend([f'<text x="600" y="133" font-size="13" fill="{muted}">{footer}</text>',
                  '</g>', '</svg>'])
    return "\n".join(parts) + "\n"


def render_outputs(root, readme, snapshot, config):
    return {
        root / "README.md": replace_regions(readme, render_regions(snapshot, config)).encode("utf-8"),
        root / "assets/profile-stats-light.svg": render_svg(snapshot).encode("utf-8"),
        root / "assets/profile-stats-dark.svg": render_svg(snapshot, dark=True).encode("utf-8"),
        root / "data/profile.json": (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    }


def apply_outputs(outputs, check=False):
    originals = {}
    for path, content in outputs.items():
        old = path.read_bytes() if path.exists() else None
        if old != content:
            originals[path] = old
    if check or not originals:
        return list(originals)

    staged, backups, temporary, replaced = {}, {}, [], []

    def stage(path, content):
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".profile-", delete=False) as handle:
            temp = Path(handle.name)
            temporary.append(temp)
            handle.write(content)
        temp.chmod((path.stat().st_mode & 0o777) if path.exists() else 0o644)
        return temp

    try:
        # 全部渲染及暂存成功后才替换；本地替换失败时回滚已替换的文件。
        for path, old in originals.items():
            if not path.parent.is_dir():
                path.parent.mkdir(parents=True, exist_ok=True)
            staged[path] = stage(path, outputs[path])
            if old is not None:
                backups[path] = stage(path, old)
        try:
            for path in originals:
                os.replace(staged[path], path)
                replaced.append(path)
        except OSError:
            for path in reversed(replaced):
                if originals[path] is None:
                    path.unlink()
                else:
                    os.replace(backups[path], path)
            raise
    finally:
        for temp in temporary:
            temp.unlink(missing_ok=True)
    return list(originals)


def read_json(path):
    return json.loads(path.read_bytes().decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="仓库根目录")
    parser.add_argument("--check", action="store_true", help="只检查差异；有差异返回 1，失败返回 2")
    parser.add_argument("--offline", type=Path, metavar="SNAPSHOT", help="使用已有公开快照，不请求 GitHub")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        readme = (root / "README.md").read_bytes().decode("utf-8")
        marker_ranges(readme)
        config = load_config(read_json(root / "profile.json"))
        if args.offline is not None:
            snapshot = validate_snapshot(read_json(args.offline), config)
        else:
            token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
            if not token:
                raise ProfileError("请通过 GITHUB_TOKEN 或 GH_TOKEN 环境变量提供 token。")
            today = datetime.now(timezone.utc).date().isoformat()
            snapshot = collect_snapshot(GitHubClient(token), config, today)
            snapshot = validate_snapshot(snapshot, config)
        changed = apply_outputs(render_outputs(root, readme, snapshot, config), check=args.check)
    except ProfileError as error:
        print(f"更新失败：{error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, UnicodeError):
        print("更新失败：本地文件读写、JSON 或输入格式无效。", file=sys.stderr)
        return 2
    if changed:
        print(("检测到更新：" if args.check else "已更新：") + "、".join(str(path.relative_to(root)) for path in changed))
        return 1 if args.check else 0
    print("公开主页数据无变化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
