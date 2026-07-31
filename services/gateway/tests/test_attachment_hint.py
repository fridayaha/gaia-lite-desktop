"""Unit tests for app.attachment_hint."""
from app.attachment_hint import format_attachment_hint, inject_attachment_hints


class TestFormatHint:
    def test_dict_attachments(self):
        assert format_attachment_hint([
            {"path": "uploads/a.pdf"}, {"path": "uploads/b.png"},
        ]) == "[Attached files: uploads/a.pdf, uploads/b.png]"

    def test_string_attachments(self):
        assert format_attachment_hint(["uploads/a.pdf", "uploads/b.png"]) == \
            "[Attached files: uploads/a.pdf, uploads/b.png]"

    def test_mixed_path_keys(self):
        # filePath / url 也能取到
        assert format_attachment_hint([{"filePath": "x/y.png"}, {"url": "z.png"}]) == \
            "[Attached files: x/y.png, z.png]"

    def test_empty(self):
        assert format_attachment_hint([]) == ""

    def test_no_extractable_path(self):
        assert format_attachment_hint([{"name": "no path"}]) == ""

    def test_skips_empty_paths(self):
        assert format_attachment_hint([{"path": ""}, {"path": "ok.png"}]) == \
            "[Attached files: ok.png]"


class TestInjectHints:
    def test_appends_to_nonempty_content(self):
        msgs = [{"role": "user", "content": "看一下", "attachments": [{"path": "uploads/a.pdf"}]}]
        out = inject_attachment_hints(msgs)
        assert out[0]["content"] == "看一下\n\n[Attached files: uploads/a.pdf]"
        assert "attachments" not in out[0]

    def test_fallback_when_content_empty(self):
        msgs = [{"role": "user", "content": "", "attachments": [{"path": "uploads/a.pdf"}]}]
        out = inject_attachment_hints(msgs)
        assert out[0]["content"] == "I've uploaded 1 file(s): uploads/a.pdf"
        assert "attachments" not in out[0]

    def test_no_attachments_passthrough(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        out = inject_attachment_hints(msgs)
        assert out == msgs

    def test_empty_attachments_strips_field(self):
        msgs = [{"role": "user", "content": "hi", "attachments": []}]
        out = inject_attachment_hints(msgs)
        assert out[0]["content"] == "hi"
        assert "attachments" not in out[0]

    def test_does_not_mutate_input(self):
        msgs = [{"role": "user", "content": "hi", "attachments": [{"path": "a.pdf"}]}]
        inject_attachment_hints(msgs)
        # 原始 message 仍保留 attachments 字段
        assert "attachments" in msgs[0]
        assert msgs[0]["content"] == "hi"

    def test_only_messages_with_attachments_get_hint(self):
        msgs = [
            {"role": "user", "content": "第一轮", "attachments": [{"path": "a.pdf"}]},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "第二轮", "attachments": [{"path": "b.pdf"}]},
        ]
        out = inject_attachment_hints(msgs)
        assert out[0]["content"] == "第一轮\n\n[Attached files: a.pdf]"
        assert out[1]["content"] == "ok"
        assert out[2]["content"] == "第二轮\n\n[Attached files: b.pdf]"
        assert all("attachments" not in m for m in out)
