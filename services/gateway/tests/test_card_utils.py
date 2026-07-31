"""extract_card_json 单测——卡片 JSON 提取 + 容错（补全缺失 ``}``）。"""

from app.channel.card_utils import extract_card_json


class TestExtractCardJson:
    """正常提取场景。"""

    def test_pure_json(self):
        """纯 JSON → 直接提取。"""
        card = '{"msgtype": "template_card", "template_card": {"card_type": "text_notice"}}'
        obj, before, after = extract_card_json(card)
        assert obj is not None
        assert obj["msgtype"] == "template_card"
        assert before == ""
        assert after == ""

    def test_json_with_leading_text(self):
        """前导文字 + JSON → before 为前导，after 为空。"""
        text = '这是报告：\n{"msgtype": "template_card", "template_card": {}}'
        obj, before, after = extract_card_json(text)
        assert obj is not None
        assert obj["msgtype"] == "template_card"
        assert "这是报告" in before
        assert after == ""

    def test_json_with_trailing_text(self):
        """JSON + 后续文字 → after 为后续。"""
        text = '{"msgtype": "template_card", "template_card": {}}\n后续说明'
        obj, before, after = extract_card_json(text)
        assert obj is not None
        assert "后续说明" in after

    def test_json_in_code_fence(self):
        """```json 代码围栏 → 正常提取。"""
        text = '```json\n{"msgtype": "template_card", "template_card": {}}\n```'
        obj, before, after = extract_card_json(text)
        assert obj is not None
        assert obj["msgtype"] == "template_card"

    def test_no_json_returns_none(self):
        """纯文本无 JSON → None。"""
        obj, before, after = extract_card_json("hello world")
        assert obj is None
        assert before == "hello world"

    def test_prose_braces_skipped(self):
        """prose 里的 {示例} 被跳过，找到后面的真 JSON。"""
        text = 'Hello {world} and {"msgtype": "template_card", "template_card": {}}'
        obj, before, after = extract_card_json(text)
        assert obj is not None
        assert obj["msgtype"] == "template_card"

    def test_required_key_absent(self):
        """JSON 不含 required_key → 跳过。"""
        text = '{"foo": "bar"}'
        obj, before, after = extract_card_json(text)
        assert obj is None


class TestExtractCardJsonRepair:
    """容错：AI 丢尾部 ``}`` 时补全修复。"""

    def test_missing_one_brace(self):
        """缺 1 个 ``}``（AI 最常见的丢字符场景）→ 补全修复。"""
        # 完整: {"msgtype":"template_card","template_card":{"card_type":"text_notice"}}}
        # 截断: 少了最后 1 个 }
        truncated = '{"msgtype": "template_card", "template_card": {"card_type": "text_notice"}}'
        obj, before, after = extract_card_json(truncated)
        assert obj is not None
        assert obj["msgtype"] == "template_card"
        assert obj["template_card"]["card_type"] == "text_notice"

    def test_missing_two_braces(self):
        """缺 2 个 ``}`` → 补全修复。"""
        truncated = '{"msgtype": "template_card", "template_card": {"card_type": "text_notice"}'
        obj, before, after = extract_card_json(truncated)
        assert obj is not None
        assert obj["msgtype"] == "template_card"

    def test_missing_three_braces(self):
        """缺 3 个 ``}`` → 补全修复。"""
        truncated = '{"msgtype": "template_card", "template_card": {"card_type": "text_notice"'
        obj, before, after = extract_card_json(truncated)
        assert obj is not None
        assert obj["msgtype"] == "template_card"

    def test_truncated_with_leading_text(self):
        """前导文字 + 截断 JSON → 补全修复，before 为前导。"""
        truncated = '报告如下：\n{"msgtype": "template_card", "template_card": {"card_type": "text_notice"}}'
        obj, before, after = extract_card_json(truncated)
        assert obj is not None
        assert obj["msgtype"] == "template_card"
        assert "报告如下" in before

    def test_real_case_test_drive_report(self):
        """真实场景：test-drive-report 卡片丢最后 ``}``（583 chars → 补全）。"""
        # 完整 JSON 结尾应是 ...1610926234373626371"}}}
        # 截断少了最后 1 个 }
        truncated = (
            '{"msgtype": "template_card", "template_card": {"card_type": "text_notice", '
            '"source": {"desc": "试驾报告系统"}, "main_title": {"title": "🚗 客户5997的试驾报告"}, '
            '"horizontal_content_list": [{"keyname": "👤 客户", "value": "客户5997 · 152****5997"}, '
            '{"keyname": "🚙 车型", "value": "M817"}, {"keyname": "🕐 时间", "value": "2026-05-27 09:00"}], '
            '"jump_list": [{"type": 1, "url": "https://mhero.dfmc.com.cn/report/1610926234373626371", "title": "查看完整报告"}], '
            '"card_action": {"type": 1, "url": "https://mhero.dfmc.com.cn/report/1610926234373626371"}}}'
        )
        obj, before, after = extract_card_json(truncated)
        assert obj is not None
        assert obj["msgtype"] == "template_card"
        assert obj["template_card"]["card_type"] == "text_notice"
        assert obj["template_card"]["main_title"]["title"] == "🚗 客户5997的试驾报告"

    def test_complete_json_not_affected(self):
        """完整 JSON 不受容错影响（走原配平路径）。"""
        complete = '{"msgtype": "template_card", "template_card": {"card_type": "text_notice"}}}'
        obj1, _, _ = extract_card_json(complete)
        assert obj1 is not None
        # 确保和补全路径结果一致
        assert obj1["msgtype"] == "template_card"

    def test_prose_without_msgtype_not_repaired(self):
        """不含 msgtype 的 prose ``{`` → 不触发补全 → None。"""
        text = "Hello {world without closing brace"
        obj, before, after = extract_card_json(text)
        assert obj is None

    def test_truncated_non_json_with_msgtype(self):
        """含 msgtype 但不是 JSON → 补全失败 → None。"""
        text = '{"msgtype": this is not valid json'
        obj, before, after = extract_card_json(text)
        assert obj is None
