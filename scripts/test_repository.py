"""Validate committed public profile data and assets without network access."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import update_profile as profile


ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_committed_snapshot_matches_configuration(self):
        config = profile.load_config(profile.read_json(ROOT / "profile.json"))
        snapshot = profile.read_json(ROOT / "data/profile.json")
        self.assertEqual(profile.validate_snapshot(snapshot, config), snapshot)
        for row in snapshot["contributions"] + snapshot["featured"]:
            self.assertIs(type(row["stars"]), int)
            self.assertGreaterEqual(row["stars"], 0)

    def test_offline_check_needs_no_token_or_network_and_writes_nothing(self):
        paths = [ROOT / "README.md", ROOT / "data/profile.json"]
        paths.extend(sorted((ROOT / "assets").glob("*.svg")))
        before = {path: path.read_bytes() for path in paths}
        stream = io.StringIO()
        with patch.dict(profile.os.environ, {}, clear=True), \
                patch.object(profile, "build_opener", side_effect=AssertionError(
                    "Repository checks must not call GitHub")) as network, \
                patch.object(profile.os, "replace") as replace, \
                redirect_stdout(stream), redirect_stderr(stream):
            status = profile.main([
                "--root", str(ROOT), "--offline", str(ROOT / "data/profile.json"),
                "--check",
            ])
        self.assertEqual(status, 0, stream.getvalue())
        network.assert_not_called()
        replace.assert_not_called()
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_profile_images_are_local_valid_self_contained_svg(self):
        svg_namespace = "http://www.w3.org/2000/svg"
        names = {
            "cover-light.svg", "cover-dark.svg",
            "profile-stats-light.svg", "profile-stats-dark.svg",
        }
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in sorted(names):
            with self.subTest(asset=name):
                self.assertIn("assets/" + name, readme)
                root = ET.parse(ROOT / "assets" / name).getroot()
                self.assertEqual(root.tag, "{" + svg_namespace + "}svg")
                self.assertTrue(root.get("viewBox"))
                for element in root.iter():
                    self.assertNotEqual(element.tag, "{" + svg_namespace + "}script")
                    for key, value in element.attrib.items():
                        self.assertFalse(key.lower().startswith("on"))
                        if key.rsplit("}", 1)[-1] == "href":
                            self.assertTrue(value.startswith("#"), value)

    def test_public_snapshot_is_readable_utf8_json(self):
        snapshot = (ROOT / "data/profile.json").read_text(encoding="utf-8")
        data = json.loads(snapshot)
        self.assertEqual(set(data), {"username", "updated_at", "contributions", "featured"})
        self.assertEqual(data["username"], "kernelvale")


if __name__ == "__main__":
    unittest.main()
