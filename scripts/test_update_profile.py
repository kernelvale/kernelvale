"""无网络测试：python3 -B -m unittest discover -s scripts -p 'test_update_profile.py'。"""

import ast
from contextlib import redirect_stderr, redirect_stdout
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET


sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_profile as profile


TODAY = "2026-11-12"
README = """# 简介

<!-- CONTRIBUTIONS:START -->
旧贡献
<!-- CONTRIBUTIONS:END -->

手工内容不变。

<!-- PROJECT-STARS:START -->
旧项目
<!-- PROJECT-STARS:END -->

尾部不变。
"""


def config(**overrides):
    raw = {
        "username": "kernelvale",
        "contribution_notes": {"KKKKhazix/AIHOT": "事件归组一致性、批量向量召回与连续合并解析"},
        "featured_repositories": ["kernelvale/demo", "kernelvale/second"],
        "max_contribution_repos": 8,
        "max_recent_prs": 4,
    }
    raw.update(overrides)
    return profile.load_config(raw)


def issue(repo="upstream/core", number=1, title="修复边界情况", merged_at="2026-11-11T12:00:00Z"):
    return {
        "number": number, "title": title, "state": "closed",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "user": {"login": "kernelvale", "email": "不应进入快照"},
        "pull_request": {"merged_at": merged_at},
        "body": "不应进入快照", "html_url": "https://不可信地址.invalid",
    }


def metadata(repo, stars=7, fork=False, private=False, language="Python", visibility=None):
    return {
        "full_name": repo, "owner": {"login": repo.split("/")[0]},
        "stargazers_count": stars, "fork": fork, "private": private,
        "language": language, "visibility": visibility or ("private" if private else "public"),
        "description": "不应进入快照",
    }


def api_for(items, repos):
    def get(path, params=None):
        if path == "/search/issues":
            start = (params["page"] - 1) * params["per_page"]
            return {"incomplete_results": False, "total_count": len(items), "items": items[start:start + 100]}
        return copy.deepcopy(repos[path.removeprefix("/repos/")])
    return Mock(get=Mock(side_effect=get))


def sample_snapshot():
    return {
        "username": "kernelvale", "updated_at": TODAY,
        "contributions": [
            {"repo": "upstream/core", "stars": 12345, "note": "主要贡献",
             "prs": [{"number": 2, "title": "新修复", "merged_at": "2026-11-12T01:00:00Z"},
                     {"number": 1, "title": "旧修复", "merged_at": "2026-11-11T01:00:00Z"}]},
            {"repo": "another/tool", "stars": 6, "note": "最近 PR 标题",
             "prs": [{"number": 3, "title": "最近 PR 标题", "merged_at": "2026-11-10T01:00:00Z"}]},
        ],
        "featured": [
            {"repo": "kernelvale/demo", "stars": 11, "language": "Python"},
            {"repo": "kernelvale/second", "stars": 13, "language": None},
        ],
    }


class SearchTests(unittest.TestCase):
    def test_all_pages_and_query(self):
        items = [issue(number=n) for n in range(1, 206)]
        api = api_for(items, {})
        self.assertEqual(profile.search_merged_prs(api, "kernelvale"), items)
        self.assertEqual(api.get.call_count, 3)
        for page, call in enumerate(api.get.call_args_list, 1):
            self.assertEqual(call.args, ("/search/issues", {
                "q": "author:kernelvale is:pr is:public is:merged",
                "per_page": 100, "page": page, "sort": "updated", "order": "desc",
            }))

    def test_empty_and_exact_search_limit(self):
        for count in (0, 100, 1000):
            with self.subTest(count=count):
                items = [issue(number=n) for n in range(1, count + 1)]
                api = api_for(items, {})
                self.assertEqual(len(profile.search_merged_prs(api, "kernelvale")), count)
                self.assertEqual(api.get.call_count, max(1, (count + 99) // 100))

    def test_incomplete_over_limit_and_invalid_pages_fail(self):
        cases = [
            {"incomplete_results": True, "total_count": 1, "items": [issue()]},
            {"incomplete_results": False, "total_count": 1001, "items": []},
            {"total_count": 0, "items": []},
            {"incomplete_results": False, "total_count": 1, "items": []},
            {"incomplete_results": False, "total_count": True, "items": []},
            {"incomplete_results": False, "total_count": 0, "items": [issue()]},
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(profile.ProfileError):
                profile.search_merged_prs(Mock(get=Mock(return_value=payload)), "kernelvale")

    def test_later_page_incomplete_or_count_changed(self):
        first = {"incomplete_results": False, "total_count": 101,
                 "items": [issue(number=n) for n in range(1, 101)]}
        for last in ({"incomplete_results": True, "total_count": 101, "items": [issue(number=101)]},
                     {"incomplete_results": False, "total_count": 102, "items": [issue(number=101)]}):
            with self.subTest(last=last), self.assertRaises(profile.ProfileError):
                profile.search_merged_prs(Mock(get=Mock(side_effect=[first, last])), "kernelvale")


class CollectionTests(unittest.TestCase):
    def test_excludes_self_forks_and_non_public_repositories(self):
        names = ["KernelVale/self", "upstream/fork", "upstream/private", "upstream/internal", "upstream/core"]
        items = [issue(repo=name) for name in names]
        repos = {
            "upstream/fork": metadata("upstream/fork", fork=True),
            "upstream/private": metadata("upstream/private", private=True),
            "upstream/internal": metadata("upstream/internal", visibility="internal"),
            "upstream/core": metadata("upstream/core", stars=12345),
        }
        api = api_for(items, repos)
        result = profile.collect_snapshot(api, config(featured_repositories=[]), TODAY)
        self.assertEqual([row["repo"] for row in result["contributions"]], ["upstream/core"])
        self.assertEqual(result["contributions"][0]["note"], "修复边界情况")
        self.assertNotIn("/repos/KernelVale/self", [call.args[0] for call in api.get.call_args_list])
        self.assertEqual(profile.metrics(result), (1, 1, 12345, 0))

    def test_discovers_new_repos_uses_notes_and_latest_merged_time(self):
        items = [
            issue("KKKKhazix/AIHOT", 1, "旧标题", "2026-10-01T01:00:00Z"),
            issue("new/upstream", 3, "自动发现", "2026-11-09T01:00:00Z"),
            issue("KKKKhazix/AIHOT", 2, "新标题", "2026-11-10T01:00:00Z"),
        ]
        repos = {name: metadata(name) for name in ("KKKKhazix/AIHOT", "new/upstream")}
        result = profile.collect_snapshot(api_for(items, repos), config(featured_repositories=[]), TODAY)
        self.assertEqual(len(result["contributions"]), 2)
        ai, new = result["contributions"]
        self.assertEqual(ai["note"], "事件归组一致性、批量向量召回与连续合并解析")
        self.assertEqual([pr["number"] for pr in ai["prs"]], [2, 1])
        self.assertEqual(new["note"], "自动发现")
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("不应进入快照", encoded)
        self.assertNotIn("不可信地址", encoded)

    def test_same_data_in_different_api_order_has_identical_outputs(self):
        cfg = config(featured_repositories=[])
        items = [issue(number=1), issue(number=2), issue("another/tool", 3)]
        repos = {name: metadata(name) for name in ("upstream/core", "another/tool")}
        first = profile.collect_snapshot(api_for(items, repos), cfg, TODAY)
        second = profile.collect_snapshot(api_for(list(reversed(items)), repos), cfg, TODAY)
        self.assertEqual(first, second)
        self.assertEqual(profile.render_outputs(Path("/unused"), README, first, cfg),
                         profile.render_outputs(Path("/unused"), README, second, cfg))

    def test_featured_preserves_config_order_and_caches_repos(self):
        repos = {name: metadata(name, stars=index) for index, name in enumerate(
            ("kernelvale/demo", "kernelvale/second", "upstream/core"), 1)}
        cfg = config(featured_repositories=["kernelvale/second", "upstream/core", "kernelvale/demo"])
        api = api_for([issue()], repos)
        result = profile.collect_snapshot(api, cfg, TODAY)
        self.assertEqual([row["repo"] for row in result["featured"]], cfg["featured_repositories"])
        self.assertEqual([row["stars"] for row in result["featured"]], [2, 3, 1])
        self.assertEqual(api.get.call_count, 4)
        self.assertEqual(profile.metrics(result), (1, 1, 3, 6))

    def test_private_featured_is_error(self):
        api = api_for([], {"kernelvale/demo": metadata("kernelvale/demo", private=True)})
        with self.assertRaises(profile.ProfileError):
            profile.collect_snapshot(api, config(featured_repositories=["kernelvale/demo"]), TODAY)

    def test_duplicate_wrong_author_unmerged_or_wrong_repo_is_error(self):
        wrong_author = issue()
        wrong_author["user"]["login"] = "someone-else"
        unmerged = issue()
        unmerged["pull_request"]["merged_at"] = None
        wrong_repo = issue()
        wrong_repo["repository_url"] = "https://evil.invalid/repos/upstream/core"
        for items in ([issue(), issue()], [wrong_author], [unmerged], [wrong_repo]):
            with self.subTest(items=items), self.assertRaises(profile.ProfileError):
                profile.collect_snapshot(api_for(items, {}), config(featured_repositories=[]), TODAY)


class RenderingTests(unittest.TestCase):
    def test_markdown_special_characters_do_not_break_table(self):
        value = "修复 | [伪链接](javascript:x) <script> & `code` *粗体* _下划线_ \\ #标题\r\n新行"
        escaped = profile.markdown(value)
        for expected in (r"\|", r"\[", r"\]", r"\(", r"\)", "&lt;script&gt;", "&amp;",
                         r"\`", r"\*", r"\_", r"\\", r"\#"):
            self.assertIn(expected, escaped)
        self.assertNotIn("\n", escaped)
        self.assertNotIn("<script>", escaped)
        snapshot = sample_snapshot()
        snapshot["contributions"][0]["note"] = value
        snapshot["contributions"][0]["prs"][0]["title"] = value
        snapshot["featured"][0]["language"] = value
        regions = profile.render_regions(snapshot, config())
        self.assertIn(escaped, regions["CONTRIBUTIONS"])
        self.assertIn(escaped, regions["PROJECT-STARS"])
        self.assertNotIn("](javascript:", regions["CONTRIBUTIONS"])

    def test_markdown_preserves_html_entities_quotes_and_marker_text(self):
        self.assertEqual(profile.markdown("\"引号\" '单引号' &"), "&quot;引号&quot; &#x27;单引号&#x27; &amp;")
        value = "<!-- CONTRIBUTIONS:END -->"
        escaped = profile.markdown(value)
        self.assertNotIn(value, escaped)
        self.assertIn("&lt;", escaped)
        self.assertIn("&gt;", escaped)
        regions = {name: escaped for name in profile.MARKERS}
        once = profile.replace_regions(README, regions)
        self.assertEqual(profile.replace_regions(once, regions), once)

    def test_markers_missing_duplicate_reversed_nested_or_crossed_fail(self):
        cstart, cend = "<!-- CONTRIBUTIONS:START -->", "<!-- CONTRIBUTIONS:END -->"
        pstart, pend = "<!-- PROJECT-STARS:START -->", "<!-- PROJECT-STARS:END -->"
        cases = [
            README.replace(cstart, ""), README.replace(pend, ""), README + cstart,
            cend + cstart + pstart + pend,
            cstart + pstart + pend + cend,
            cstart + pstart + cend + pend,
        ]
        for readme in cases:
            with self.subTest(readme=readme), self.assertRaises(profile.ProfileError):
                profile.replace_regions(readme, {name: "内容" for name in profile.MARKERS})

    def test_rendering_is_idempotent_and_preserves_unmanaged_text(self):
        snapshot = sample_snapshot()
        regions = profile.render_regions(snapshot, config())
        once = profile.replace_regions(README, regions)
        self.assertEqual(profile.replace_regions(once, regions), once)
        self.assertTrue(once.startswith("# 简介\n\n<!-- CONTRIBUTIONS:START -->"))
        self.assertIn("\n\n手工内容不变。\n\n", once)
        self.assertTrue(once.endswith("\n\n尾部不变。\n"))
        self.assertIn(f"数据日期（UTC）：{TODAY}", once)
        self.assertIn("Stars 为仓库指标，非个人获得", once)
        self.assertNotIn("2026-11-12T", once)
        self.assertIn("| 12345 |", once)
        self.assertNotIn("12.3k", once)
        self.assertIn("[demo](https://github.com/kernelvale/demo)", regions["PROJECT-STARS"])
        self.assertNotIn("[kernelvale/", regions["PROJECT-STARS"])
        self.assertLess(regions["PROJECT-STARS"].index("[demo]"), regions["PROJECT-STARS"].index("[second]"))
        self.assertIn("/pull/2)", once)
        self.assertIn("/pull/1)", once)

    def test_four_svg_metrics_use_all_rows_not_display_limits(self):
        snapshot = sample_snapshot()
        cfg = config(max_contribution_repos=1, max_recent_prs=1)
        self.assertEqual(profile.metrics(snapshot), (2, 3, 12351, 24))
        regions = profile.render_regions(snapshot, cfg)
        self.assertNotIn("[another/tool]", regions["CONTRIBUTIONS"])
        self.assertEqual(regions["CONTRIBUTIONS"].count("\n- "), 1)
        for dark in (False, True):
            svg = profile.render_svg(snapshot, dark)
            self.assertEqual(svg, profile.render_svg(snapshot, dark))
            root = ET.fromstring(svg)
            self.assertEqual((root.get("width"), root.get("height")), ("1200", "150"))
            ns = {"svg": "http://www.w3.org/2000/svg"}
            texts = [node.text for node in root.findall(".//svg:text", ns)]
            for value in ("2", "3", "12351", "24", "外部贡献仓库数", "已合并外部PR数",
                          "贡献仓库Stars合计", "精选项目Stars合计"):
                self.assertIn(value, texts)
            self.assertIn("非个人获得", svg)
            self.assertNotIn("<image", svg)
            self.assertNotIn("href=", svg)
        self.assertNotEqual(profile.render_svg(snapshot), profile.render_svg(snapshot, True))

    def test_empty_stats_are_valid(self):
        snapshot = {"username": "kernelvale", "updated_at": TODAY, "contributions": [], "featured": []}
        self.assertEqual(profile.metrics(snapshot), (0, 0, 0, 0))
        self.assertIn("| 公共上游仓库", profile.render_regions(snapshot, config())["CONTRIBUTIONS"])
        ET.fromstring(profile.render_svg(snapshot))

    def test_snapshot_allowlist_sorting_and_strict_validation(self):
        snapshot = sample_snapshot()
        dirty = copy.deepcopy(snapshot)
        dirty["private_information"] = "不应进入快照"
        dirty["contributions"][0]["body"] = "不应进入快照"
        dirty["contributions"][0]["prs"][0]["html_url"] = "不应进入快照"
        dirty["featured"][0]["secret"] = "不应进入快照"
        dirty["featured"].reverse()
        self.assertEqual(profile.validate_snapshot(dirty, config()), snapshot)
        for change in (lambda s: s.update(updated_at="2026-99-01"),
                       lambda s: s.update(username="other"),
                       lambda s: s["contributions"][0].update(repo="kernelvale/self"),
                       lambda s: s["contributions"][0].update(stars=True),
                       lambda s: s["contributions"][0].update(prs=[]),
                       lambda s: s["featured"].pop()):
            invalid = copy.deepcopy(snapshot)
            change(invalid)
            with self.subTest(invalid=invalid), self.assertRaises(profile.ProfileError):
                profile.validate_snapshot(invalid, config())

    def test_config_rejects_unsafe_repository_names_and_non_integer_limits(self):
        for changes in ({"featured_repositories": ["owner/../private"]},
                        {"featured_repositories": ["owner/.."]},
                        {"featured_repositories": ["owner/repo", "OWNER/REPO"]},
                        {"username": "bad user"}, {"max_recent_prs": True},
                        {"max_contribution_repos": -1}):
            with self.subTest(changes=changes), self.assertRaises(profile.ProfileError):
                config(**changes)


class HttpTests(unittest.TestCase):
    def response(self, payload):
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def test_only_get_allowed_host_headers_and_redirects_blocked(self):
        client = profile.GitHubClient("仅测试的占位凭据")
        client._opener = Mock()
        client._opener.open.return_value = self.response({"ok": True})
        self.assertEqual(client.get("/repos/upstream/core"), {"ok": True})
        request = client._opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "https://api.github.com/repos/upstream/core")
        self.assertEqual(request.get_header("Authorization"), "Bearer 仅测试的占位凭据")
        self.assertEqual(client._opener.open.call_args.kwargs["timeout"], 30)
        self.assertIsNone(profile.NoRedirect().redirect_request(request, None, 302, "", {}, "https://evil.invalid"))
        for path in ("https://evil.invalid", "/user", "/repos/owner/../secrets", "/repos/owner/.."):
            with self.subTest(path=path), self.assertRaises(profile.ProfileError):
                client.get(path)
        self.assertEqual(client._opener.open.call_count, 1)

    @patch.object(profile.time, "sleep")
    def test_transient_http_and_network_errors_retry_with_bound(self, sleep):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                client = profile.GitHubClient("占位凭据")
                client._opener = Mock()
                error = HTTPError("https://api.github.com", status, "敏感错误正文", {}, None)
                client._opener.open.side_effect = [error, self.response({"ok": True})]
                self.assertEqual(client.get("/repos/upstream/core"), {"ok": True})
                self.assertEqual(client._opener.open.call_count, 2)
        client = profile.GitHubClient("占位凭据")
        client._opener = Mock()
        client._opener.open.side_effect = URLError("占位凭据")
        sleep.reset_mock()
        with self.assertRaises(profile.ProfileError) as error:
            client.get("/repos/upstream/core")
        self.assertNotIn("占位凭据", str(error.exception))
        self.assertEqual(client._opener.open.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    @patch.object(profile.time, "time", return_value=1800000000)
    @patch.object(profile.time, "sleep")
    def test_rate_limits_wait_for_headers_before_retrying(self, sleep, clock):
        cases = [
            (429, {"Retry-After": "7"}, 7),
            (403, {"Retry-After": "7"}, 7),
            (403, {"rEtRy-AfTeR": "7"}, 7),
            (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000012"}, 12),
            (429, {"X-RateLimit-Reset": "1800000012"}, 12),
            (429, {"Retry-After": "Fri, 15 Jan 2027 08:00:09 GMT"}, 9),
            (429, {"Retry-After": "30"}, 30),
            (429, {"Retry-After": "0"}, 0),
            (429, {"Retry-After": "7", "X-RateLimit-Remaining": "4", "X-RateLimit-Reset": "1800003600"}, 7),
            (403, {"Retry-After": "7", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000012"}, 12),
        ]
        for status, headers, delay in cases:
            with self.subTest(status=status, headers=headers):
                sleep.reset_mock()
                client = profile.GitHubClient("占位凭据")
                client._opener = Mock()
                error = HTTPError("https://api.github.com", status, "敏感错误正文", headers, None)
                response = self.response({"ok": True})

                def open_request(*args, **kwargs):
                    if client._opener.open.call_count == 1:
                        sleep.assert_not_called()
                        raise error
                    sleep.assert_called_once_with(delay)
                    return response

                client._opener.open.side_effect = open_request
                self.assertEqual(client.get("/repos/upstream/core"), {"ok": True})
                self.assertEqual(client._opener.open.call_count, 2)

    @patch.object(profile.time, "time", return_value=1800000000)
    @patch.object(profile.time, "sleep")
    def test_rate_limit_over_budget_missing_or_invalid_headers_fail_without_retry(self, sleep, clock):
        cases = [
            (429, {}), (429, None),
            (403, {"X-RateLimit-Remaining": "0"}),
            (429, {"Retry-After": "31"}),
            (403, {"Retry-After": "60"}),
            (429, {"X-RateLimit-Reset": "1800000031"}),
            (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000031"}),
            (429, {"Retry-After": "Fri, 15 Jan 2027 08:01:00 GMT"}),
            (429, {"Retry-After": "invalid"}),
            (429, {"Retry-After": "-1"}),
            (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "invalid"}),
            (403, {"Retry-After": "7", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000060"}),
        ]
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                sleep.reset_mock()
                client = profile.GitHubClient("占位凭据")
                client._opener = Mock()
                client._opener.open.side_effect = HTTPError("https://api.github.com", status, "占位凭据", headers, None)
                with self.assertRaises(profile.ProfileError) as error:
                    client.get("/repos/upstream/core")
                self.assertNotIn("占位凭据", str(error.exception))
                self.assertEqual(client._opener.open.call_count, 1)
                sleep.assert_not_called()

    @patch.object(profile.time, "sleep")
    def test_ordinary_403_with_remaining_quota_never_retries(self, sleep):
        client = profile.GitHubClient("占位凭据")
        client._opener = Mock()
        client._opener.open.side_effect = HTTPError("https://api.github.com", 403, "占位凭据", {
            "X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "1800000000",
        }, None)
        with self.assertRaises(profile.ProfileError):
            client.get("/repos/upstream/core")
        self.assertEqual(client._opener.open.call_count, 1)
        sleep.assert_not_called()

    @patch.object(profile.time, "sleep")
    def test_wait_budget_is_cumulative_including_503_backoff(self, sleep):
        for first_status, first_headers, first_delay in ((429, {"Retry-After": "20"}, 20), (503, {}, 1)):
            with self.subTest(first_status=first_status):
                sleep.reset_mock()
                client = profile.GitHubClient("占位凭据")
                client._opener = Mock()
                client._opener.open.side_effect = [
                    HTTPError("https://api.github.com", first_status, "", first_headers, None),
                    HTTPError("https://api.github.com", 429, "", {"Retry-After": "30"}, None),
                    self.response({"ok": True}),
                ]
                with self.assertRaises(profile.ProfileError):
                    client.get("/repos/upstream/core")
                self.assertEqual(client._opener.open.call_count, 2)
                sleep.assert_called_once_with(first_delay)

    @patch.object(profile.time, "sleep")
    def test_cumulative_wait_exact_budget_succeeds(self, sleep):
        client = profile.GitHubClient("占位凭据")
        client._opener = Mock()
        client._opener.open.side_effect = [
            HTTPError("https://api.github.com", 403, "", {"Retry-After": "20"}, None),
            HTTPError("https://api.github.com", 429, "", {"Retry-After": "10"}, None),
            self.response({"ok": True}),
        ]
        self.assertEqual(client.get("/repos/upstream/core"), {"ok": True})
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [20, 10])
        self.assertEqual(client._opener.open.call_count, 3)

    @patch.object(profile.time, "sleep")
    def test_503_and_repeated_rate_limits_still_have_three_attempt_bound(self, sleep):
        for status, headers, expected in ((503, {}, [1, 2]), (429, {"Retry-After": "1"}, [1, 1])):
            with self.subTest(status=status):
                sleep.reset_mock()
                client = profile.GitHubClient("占位凭据")
                client._opener = Mock()
                client._opener.open.side_effect = HTTPError("https://api.github.com", status, "", headers, None)
                with self.assertRaises(profile.ProfileError):
                    client.get("/repos/upstream/core")
                self.assertEqual(client._opener.open.call_count, 3)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], expected)

    @patch.object(profile.time, "sleep")
    def test_permanent_failure_and_invalid_json_are_not_retried(self, sleep):
        for response in (HTTPError("https://api.github.com", 401, "占位凭据", {}, None),
                         HTTPError("https://api.github.com", 403, "占位凭据", {}, None),
                         HTTPError("https://api.github.com", 302, "占位凭据", {}, None),
                         io.BytesIO(b"not json"), self.response([])):
            with self.subTest(response=response):
                client = profile.GitHubClient("占位凭据")
                client._opener = Mock()
                if isinstance(response, Exception):
                    client._opener.open.side_effect = response
                else:
                    client._opener.open.return_value = response
                with self.assertRaises(profile.ProfileError) as error:
                    client.get("/repos/upstream/core")
                self.assertNotIn("占位凭据", str(error.exception))
                self.assertEqual(client._opener.open.call_count, 1)
        sleep.assert_not_called()


class MainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "README.md").write_text(README, encoding="utf-8")
        (self.root / "profile.json").write_text(json.dumps(config()), encoding="utf-8")
        for directory in ("assets", "data"):
            (self.root / directory).mkdir()
        for name in ("assets/profile-stats-light.svg", "assets/profile-stats-dark.svg", "data/profile.json"):
            path = self.root / name
            path.write_text("旧文件内容", encoding="utf-8")
        # 所有测试默认禁止真实网络，避免 mock 遗漏导致请求外部服务。
        self.network = patch.object(profile, "build_opener", side_effect=AssertionError("测试禁止真实网络"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = profile.main(["--root", str(self.root), *args])
        return result, out.getvalue() + err.getvalue()

    def offline_input(self):
        path = self.root / "public-snapshot.json"
        path.write_text(json.dumps(sample_snapshot()), encoding="utf-8")
        return str(path)

    def test_offline_check_no_writes_then_idempotent_updates(self):
        source = self.offline_input()
        before = self.files()
        result, _ = self.run_main("--offline", source, "--check")
        self.assertEqual(result, 1)
        self.assertEqual(before, self.files())
        self.assertEqual(self.run_main("--offline", source)[0], 0)
        saved = self.files()
        self.assertEqual(set(json.loads(saved["data/profile.json"])),
                         {"username", "updated_at", "contributions", "featured"})
        mtimes = {name: (self.root / name).stat().st_mtime_ns for name in saved}
        with patch.object(profile.os, "replace") as replace:
            self.assertEqual(self.run_main("--offline", source)[0], 0)
            self.assertEqual(self.run_main("--offline", source, "--check")[0], 0)
            replace.assert_not_called()
        self.assertEqual(saved, self.files())
        self.assertEqual(mtimes, {name: (self.root / name).stat().st_mtime_ns for name in saved})

    def test_check_missing_outputs_creates_no_directories(self):
        source = self.offline_input()
        for path in list((self.root / "assets").iterdir()) + list((self.root / "data").iterdir()):
            path.unlink()
        (self.root / "assets").rmdir()
        (self.root / "data").rmdir()
        self.assertEqual(self.run_main("--offline", source, "--check")[0], 1)
        self.assertFalse((self.root / "assets").exists())
        self.assertFalse((self.root / "data").exists())
        self.assertEqual(self.run_main("--offline", source)[0], 0)
        self.assertTrue((self.root / "assets/profile-stats-light.svg").exists())

    def test_api_failure_after_search_leaves_all_existing_files_unchanged(self):
        api = Mock()
        api.get.side_effect = [
            {"incomplete_results": False, "total_count": 1, "items": [issue()]},
            profile.ProfileError("GitHub API 请求失败（HTTP 503）。"),
        ]
        before = self.files()
        with patch.dict(profile.os.environ, {"GITHUB_TOKEN": "测试凭据不应输出"}, clear=True), \
                patch.object(profile, "GitHubClient", return_value=api):
            result, output = self.run_main()
        self.assertEqual(result, 2)
        self.assertEqual(api.get.call_count, 2)
        self.assertEqual(before, self.files())
        self.assertNotIn("测试凭据不应输出", output)

    @patch.object(profile.time, "sleep")
    def test_rate_limit_budget_failure_preserves_existing_files(self, sleep):
        before = self.files()
        for status, headers in ((429, {}), (403, {"Retry-After": "31"})):
            with self.subTest(status=status):
                opener = Mock()
                opener.open.side_effect = [
                    io.BytesIO(json.dumps({"incomplete_results": False, "total_count": 1,
                                           "items": [issue()]}).encode("utf-8")),
                    HTTPError("https://api.github.com", status, "测试凭据不应输出", headers, None),
                ]
                with patch.object(profile, "build_opener", return_value=opener):
                    client = profile.GitHubClient("测试凭据不应输出")
                with patch.dict(profile.os.environ, {"GH_TOKEN": "测试凭据不应输出"}, clear=True), \
                        patch.object(profile, "GitHubClient", return_value=client):
                    result, output = self.run_main()
                self.assertEqual(result, 2)
                self.assertIn("30 秒预算", output)
                self.assertNotIn("测试凭据不应输出", output)
                self.assertEqual(before, self.files())
                self.assertEqual(opener.open.call_count, 2)
                sleep.assert_not_called()

    def test_late_featured_failure_leaves_all_files_unchanged(self):
        api = api_for([issue()], {"upstream/core": metadata("upstream/core"),
                                  "kernelvale/demo": metadata("kernelvale/demo")})
        original = api.get.side_effect

        def get(path, params=None):
            if path == "/repos/kernelvale/second":
                raise profile.ProfileError("GitHub API 请求失败（HTTP 404）。")
            return original(path, params)

        api.get.side_effect = get
        before = self.files()
        with patch.dict(profile.os.environ, {"GH_TOKEN": "测试凭据"}, clear=True), \
                patch.object(profile, "GitHubClient", return_value=api):
            self.assertEqual(self.run_main()[0], 2)
        self.assertEqual(before, self.files())

    def test_incomplete_and_over_limit_leave_all_files_unchanged(self):
        before = self.files()
        for incomplete, total in ((True, 1), (False, 1001)):
            api = Mock(get=Mock(return_value={"incomplete_results": incomplete, "total_count": total, "items": []}))
            with self.subTest(incomplete=incomplete, total=total), \
                    patch.dict(profile.os.environ, {"GH_TOKEN": "测试凭据"}, clear=True), \
                    patch.object(profile, "GitHubClient", return_value=api):
                self.assertEqual(self.run_main()[0], 2)
                self.assertEqual(before, self.files())

    def test_invalid_readme_or_missing_token_never_calls_api(self):
        before = self.files()
        with patch.dict(profile.os.environ, {}, clear=True), patch.object(profile, "GitHubClient") as client:
            self.assertEqual(self.run_main()[0], 2)
            client.assert_not_called()
        self.assertEqual(before, self.files())
        (self.root / "README.md").write_text("缺少 marker", encoding="utf-8")
        before = self.files()
        with patch.dict(profile.os.environ, {"GITHUB_TOKEN": "测试凭据"}, clear=True), \
                patch.object(profile, "GitHubClient") as client:
            self.assertEqual(self.run_main()[0], 2)
            client.assert_not_called()
        self.assertEqual(before, self.files())

    def test_token_precedence_daily_utc_date_and_public_snapshot(self):
        repos = {name: metadata(name) for name in ("upstream/core", "kernelvale/demo", "kernelvale/second")}
        for environment, expected in (({"GITHUB_TOKEN": "首选测试凭据", "GH_TOKEN": "备用测试凭据"}, "首选测试凭据"),
                                      ({"GH_TOKEN": "备用测试凭据"}, "备用测试凭据"),
                                      ({"GITHUB_TOKEN": " ", "GH_TOKEN": "备用测试凭据"}, "备用测试凭据")):
            api = api_for([issue()], repos)
            with self.subTest(environment=environment), patch.dict(profile.os.environ, environment, clear=True), \
                    patch.object(profile, "GitHubClient", return_value=api) as client, \
                    patch.object(profile, "datetime") as clock:
                clock.now.return_value = datetime(2026, 11, 12, 23, 59, 59, tzinfo=timezone.utc)
                clock.fromisoformat.side_effect = datetime.fromisoformat
                result, output = self.run_main()
                self.assertEqual(result, 0)
                client.assert_called_once_with(expected)
                clock.now.assert_called_once_with(timezone.utc)
                self.assertNotIn(expected, output)
                saved = (self.root / "data/profile.json").read_text(encoding="utf-8")
                self.assertNotIn(expected, saved)
                self.assertNotIn("不应进入快照", saved)
                self.assertEqual(json.loads(saved)["updated_at"], TODAY)
                self.assertNotIn("23:59", saved)

    def test_staging_failure_and_replace_failure_preserve_old_files(self):
        source = self.offline_input()
        before = self.files()
        with patch.object(profile.tempfile, "NamedTemporaryFile", side_effect=OSError("无法暂存")):
            self.assertEqual(self.run_main("--offline", source)[0], 2)
        self.assertEqual(before, self.files())
        real_replace = profile.os.replace
        calls = 0

        def replace(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("模拟一次替换失败")
            return real_replace(src, dst)

        with patch.object(profile.os, "replace", side_effect=replace):
            self.assertEqual(self.run_main("--offline", source)[0], 2)
        self.assertEqual(before, self.files())


class CompatibilityTests(unittest.TestCase):
    def test_files_parse_as_python_312(self):
        for path in (Path(profile.__file__), Path(__file__)):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 12))


if __name__ == "__main__":
    unittest.main()
