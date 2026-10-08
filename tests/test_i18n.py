import ast
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "web" / "locales"
LANGUAGES = {"en", "de", "es", "fr", "it", "pt", "sk", "sv"}
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}", re.I)


def server_template(node):
    """Recover the actual exception text, including concatenation and f-strings."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(server_template(part) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        expression = ast.unparse(node.value)
        names = {
            "args[0]": "command", "result.stderr.strip()[:300]": "error",
            "desired_gid": "gid", "desired_uid": "uid", "conflict.gr_name": "name",
            "conflict.pw_name": "name", "MIN_PASSWORD": "minimum",
            "process.returncode": "code", "mdns.returncode": "code",
            "os.strerror(ctypes.get_errno())": "error",
        }
        return "{" + names.get(expression, expression) + "}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        prefix = server_template(node.left)
        suffixes = {"Folders are not mounted by Docker/OCI: ": "folders",
                    "Unknown SMB users: ": "users", "Missing mounts: ": "mounts"}
        if prefix in suffixes:
            return prefix + "{" + suffixes[prefix] + "}"
        return prefix + server_template(node.right)
    raise AssertionError(f"Uncovered server message expression: {ast.unparse(node)}")


def server_messages():
    messages = set()
    for path in (ROOT / "sharecovex").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                if isinstance(node.exc.func, ast.Name) and node.exc.func.id in {"ValueError", "RuntimeError", "SystemExit"}:
                    argument = node.exc.args[0]
                    # vfs_handle_error is collected below, at its return sites.
                    if isinstance(argument, ast.Name) and argument.id == "issue":
                        continue
                    message = server_template(argument)
                    if node.exc.func.id == "SystemExit":
                        message = message.replace("{minimum}", "{MIN_PASSWORD}")
                    messages.add(message)
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "error" and isinstance(value, (ast.Constant, ast.JoinedStr)):
                        if not isinstance(value, ast.Constant) or isinstance(value.value, str):
                            messages.add(server_template(value))
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Subscript) and ast.unparse(target.value) == "self.errors"
                for target in node.targets
            ) and isinstance(node.value, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                messages.add(server_template(node.value))
            if isinstance(node, ast.FunctionDef) and node.name == "vfs_handle_error":
                for child in ast.walk(node):
                    if isinstance(child, ast.Return) and isinstance(child.value, (ast.JoinedStr, ast.Constant)):
                        if not isinstance(child.value, ast.Constant) or isinstance(child.value.value, str):
                            messages.add(server_template(child.value))
    messages.add("Process running but not listening")
    return messages


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.messages = set()
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
        for name, value in attrs:
            if name in {"aria-label", "title", "placeholder"} and value:
                self.messages.add(value)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored -= 1

    def handle_data(self, data):
        text = " ".join(data.split())
        if text and not self.ignored:
            self.messages.add(text)


class I18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = {}
        for language in sorted(LANGUAGES):
            def unique_keys(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise AssertionError(f"Duplicate key in {language}: {key}")
                    result[key] = value
                return result
            cls.catalogs[language] = json.loads(
                (LOCALES / f"{language}.json").read_text(), object_pairs_hook=unique_keys)

    def assert_covered(self, messages):
        for language, catalog in self.catalogs.items():
            self.assertFalse(messages - catalog.keys(), f"{language}: missing {sorted(messages - catalog.keys())}")

    def test_catalogs_and_placeholder_parity(self):
        self.assertEqual({p.stem for p in LOCALES.glob("*.json")}, LANGUAGES)
        for language, catalog in self.catalogs.items():
            self.assertEqual(self.catalogs["en"].keys(), catalog.keys(), language)
        for language, catalog in self.catalogs.items():
            for source, translation in catalog.items():
                self.assertIsInstance(translation, str)
                self.assertTrue(translation.strip(), (language, source))
                self.assertEqual(set(PLACEHOLDER.findall(source)), set(PLACEHOLDER.findall(translation)),
                                 (language, source))

    def test_every_server_error_is_covered(self):
        self.assert_covered(server_messages())

    def test_every_dynamic_ui_template_is_covered(self):
        source = (ROOT / "web/app.js").read_text()
        templates = set(re.findall(r"formatMessage\('([^']+)'", source))
        self.assertGreater(len(templates), 15)
        self.assert_covered(templates)
        # Templates containing language belong in formatMessage, not ad-hoc interpolation.
        interpolations = re.findall(r"`((?:[^`]|`NFSv\$\{item\}`)*?)`", source)
        for template in interpolations:
            literal = re.sub(r"\$\{[^{}]*\}", "", template)
            if literal.startswith(("/", "#", "[", "route-", "mount-", "status ")) or "<span>" in literal:
                continue
            self.assertFalse(re.search(r"(?:[A-Za-zÀ-ÿ]{3,} )", literal), template)

    def test_static_javascript_messages_are_covered(self):
        source = (ROOT / "web/app.js").read_text()
        # Scan all string literals rather than just known message call sites:
        # this includes ternary branches, tooltips and account-form labels.
        literals = re.findall(r"'((?:\\.|[^'\\\n])*)'", source)
        technical = {" + ", ", ", " editing", " expandable", " mount-root", " open", " selected",
                     "message error", "route-action edit", "route-action save", "route-permissions not-writable",
                     "Failed to fetch", "Load failed", "NetworkError when attempting to fetch resource.",
                     "NFSv3 + NFSv4", "SMB 2.0.2", "SMB 2.1", "SMB 3.0", "SMB 3.0.2", "SMB 3.1.1"}
        messages = {text.strip() for text in literals if " " in text and text not in technical
                    and not any(marker in text for marker in ("<", "${", "/api/"))}
        self.assert_covered(messages)

    def test_static_ui_text_is_covered(self):
        parser = VisibleHTML()
        parser.feed((ROOT / "web/index.html").read_text())
        # Product names, numeric protocol versions and examples are data, not language.
        data = {"ShareCoveX", "SMB", "NFS", "SMB / NFS", "SMB 2.0.2", "SMB 2.1", "SMB 3.0",
                "SMB 3.0.2", "SMB 3.1.1", "NFSv3", "NFSv4", "UID", "GID", "1000", "1000:1000",
                "192.168.0.0/24", "0", "750", "300", "/shares", "—"}
        self.assert_covered(parser.messages - data)


if __name__ == "__main__":
    unittest.main()
