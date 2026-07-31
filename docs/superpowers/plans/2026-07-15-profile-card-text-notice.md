# 画像卡改 text_notice 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 customer-profile-update 的画像卡从 button_interaction 改为 text_notice（去"换一个"/"更新画像"，加"查看完整画像"跳转 + \n sub_title），减时延 + 美化。

**Architecture:** 改 build_card.py 的 build_profile_card 输出 text_notice 结构；validate_card.py 的 _sanitize_profile 放行 text_notice + 校验 jump_list/card_action url；SKILL.md/references 同步指令；测试 TDD。

**Tech Stack:** Python 3 stdlib（skill 脚本），unittest（测试），WeCom template_card text_notice。

## Global Constraints

- skill 脚本只用 Python 标准库（无第三方依赖）
- 测试用 `python3 -m unittest`（独立于 make test）
- WeCom text_notice 的 `card_action` 是必填项（type:1 + url）
- `sub_title_text` ≤112 字
- `horizontal_content_list` ≤6 项，keyname ≤5 字，value ≤26 字
- 选择卡（button_interaction）+ 错误卡（button_interaction）不变
- Conventional Commits 中文 + HAPI Co-Authored-By
- 提交到本地 develop 分支（按项目工作流）

---

### Task 1: build_profile_card 改 text_notice

**Files:**
- Modify: `assets/skills/automotive/customer-profile-update/scripts/build_card.py:79-127`
- Test: `assets/skills/automotive/customer-profile-update/scripts/tests/test_build_card.py`

**Interfaces:**
- Consumes: `data` dict（profile.py 输出：fields/phone/customer_name/update_url）
- Produces: `build_profile_card(data)` → text_notice card dict（card_type="text_notice", jump_list, card_action, \n sub_title_text, 无 button_list/task_id）

- [ ] **Step 1: 写失败测试**

在 `test_build_card.py` 的 `TestBuildProfileCard`（或新建）中加：

```python
class TestBuildProfileCardTextNotice(unittest.TestCase):
    def setUp(self):
        self.data = {
            "fields": {
                "deal_level": "A", "overall_tag": "务实家用型",
                "personality_summary": "务实注重家庭",
                "intended_model": "追光", "budget_range": "30-40万",
                "current_stage": "需求确认", "breakthrough_point": "金融方案",
                "motivations": "家庭代步+安全", "preferences": "空间/油耗",
                "resistances": "价格/品牌力",
            },
            "phone": "13912345678", "customer_name": "客户5678",
            "update_url": "http://example.com/profile/13912345678",
        }

    def test_card_type_text_notice(self):
        card = build_profile_card(self.data)
        self.assertEqual(card["template_card"]["card_type"], "text_notice")

    def test_no_button_list_no_task_id(self):
        tc = build_profile_card(self.data)["template_card"]
        self.assertNotIn("button_list", tc)
        self.assertNotIn("task_id", tc)

    def test_jump_list_view_profile(self):
        tc = build_profile_card(self.data)["template_card"]
        self.assertIn("jump_list", tc)
        self.assertEqual(tc["jump_list"][0]["title"], "查看完整画像")
        self.assertEqual(tc["jump_list"][0]["url"], self.data["update_url"])

    def test_card_action_required(self):
        tc = build_profile_card(self.data)["template_card"]
        self.assertEqual(tc["card_action"]["type"], 1)
        self.assertEqual(tc["card_action"]["url"], self.data["update_url"])

    def test_sub_title_newline(self):
        tc = build_profile_card(self.data)["template_card"]
        self.assertIn("\n", tc["sub_title_text"])
        self.assertIn("动机：", tc["sub_title_text"])
        self.assertIn("偏好：", tc["sub_title_text"])
        self.assertIn("抗性：", tc["sub_title_text"])

    def test_no_update_profile_hcl_row(self):
        tc = build_profile_card(self.data)["template_card"]
        for item in tc["horizontal_content_list"]:
            self.assertNotIn("url", item)
            self.assertNotIn("type", item)

    def test_no_update_url_placeholder_card_action(self):
        data = dict(self.data, update_url="")
        tc = build_profile_card(data)["template_card"]
        self.assertIn("card_action", tc)  # 必填，缺 url 用占位
        self.assertNotIn("jump_list", tc)  # 无 url 不加 jump_list
```

在文件头加 `from build_card import build_profile_card`（如已有则跳过）。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd assets/skills/automotive/customer-profile-update/scripts && python3 -m pytest tests/test_build_card.py::TestBuildProfileCardTextNotice -v 2>/dev/null || python3 -m unittest tests.test_build_card.TestBuildProfileCardTextNotice -v
```
Expected: FAIL（card_type 是 button_interaction 不是 text_notice）

- [ ] **Step 3: 实现 build_profile_card text_notice**

替换 `build_card.py` 的 `build_profile_card` 函数（第 79-127 行）为：

```python
def build_profile_card(data):
    """单客户画像 → text_notice 画像卡。data 为 profile.py 输出（fields/update_url/phone/customer_name）。

    text_notice 结构（参考试驾报告卡）：horizontal_content_list 展示画像字段，
    jump_list + card_action 做「查看完整画像」跳转，sub_title_text 用 \n 换行展示
    动机/偏好/抗性。无 button_list（无「换一个」）、无 task_id、无「更新画像」hcl 行。
    """
    fields = data.get("fields", {}) or {}
    phone = data.get("phone", "")
    customer_name = data.get("customer_name") or f"客户{phone[-4:]}" if phone else "客户"
    update_url = data.get("update_url", "")

    title = f"{customer_name} · {mask_phone(phone)}"
    deal_level = fields.get("deal_level", "")
    personality = (fields.get("personality_summary", "") or "")[:20]
    desc = f"{deal_level}级意向 · {personality}" if deal_level else personality

    # 字段取舍优先级（取前 5 非空）
    priority = [
        ("整体标签", fields.get("overall_tag", "")),
        ("意向车型", fields.get("intended_model", "")),
        ("预算区间", fields.get("budget_range", "")),
        ("购买阶段", fields.get("current_stage", "")),
        ("突破策略", fields.get("breakthrough_point", "")),
    ]
    hcl = [{"keyname": k[:5], "value": (str(v)[:26] if v else "—")} for k, v in priority if v]
    hcl = hcl[:5]

    # sub_title_text：动机/偏好/抗性 \n 换行
    motivations = fields.get("motivations", "")
    preferences = fields.get("preferences", "")
    resistances = fields.get("resistances", "")
    parts = []
    if motivations:
        parts.append(f"动机：{motivations}")
    if preferences:
        parts.append(f"偏好：{preferences}")
    if resistances:
        parts.append(f"抗性：{resistances}")
    sub_title = "\n".join(parts)[:112] if parts else "暂无动机/偏好/抗性摘要"

    card = {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "source": {"desc": "客户画像"},
            "main_title": {"title": title[:26], "desc": desc} if desc else {"title": title[:26]},
            "sub_title_text": sub_title,
            "horizontal_content_list": hcl,
        },
    }
    # text_notice 必填 card_action；有 update_url 时加 jump_list「查看完整画像」
    if update_url:
        card["template_card"]["jump_list"] = [{"type": 1, "url": update_url, "title": "查看完整画像"}]
        card["template_card"]["card_action"] = {"type": 1, "url": update_url}
    else:
        card["template_card"]["card_action"] = {"type": 1, "url": "https://work.weixin.qq.com"}
    return card
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd assets/skills/automotive/customer-profile-update/scripts && python3 -m unittest tests.test_build_card.TestBuildProfileCardTextNotice -v
```
Expected: 7 tests PASS

- [ ] **Step 5: 跑全量 build_card 测试确认无回归**

```bash
cd assets/skills/automotive/customer-profile-update/scripts && python3 -m unittest tests.test_build_card -v
```
Expected: ALL PASS（包括原有的 selection/error card 测试）

- [ ] **Step 6: 提交**

```bash
cd /home/ubuntu/union_agent
git add assets/skills/automotive/customer-profile-update/scripts/build_card.py assets/skills/automotive/customer-profile-update/scripts/tests/test_build_card.py
git commit -m "feat(skill): build_profile_card 改 text_notice（去换一个/更新画像，加查看完整画像跳转+\n sub_title）

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 2: validate_card 放行 text_notice 画像卡

**Files:**
- Modify: `assets/skills/automotive/customer-profile-update/scripts/validate_card.py:107-152`
- Test: `assets/skills/automotive/customer-profile-update/scripts/tests/test_validate_card.py`

**Interfaces:**
- Consumes: `build_profile_card`（Task 1 的 text_notice 输出）
- Produces: `_sanitize_profile(card, data)` 放行 text_notice + 校验 jump_list/card_action url

- [ ] **Step 1: 写失败测试**

在 `test_validate_card.py` 加（在 TestSanitizeProfile 类中或新建）：

```python
class TestSanitizeProfileTextNotice(unittest.TestCase):
    def setUp(self):
        self.data = {
            "ok": True, "has_profile": True,
            "fields": {"deal_level": "A", "overall_tag": "务实",
                       "motivations": "代步", "preferences": "空间",
                       "resistances": "价格"},
            "phone": "13912345678", "customer_name": "客户5678",
            "update_url": "http://example.com/profile/13912345678",
        }

    def test_text_notice_draft_passes(self):
        """text_notice 画像卡草稿（url 正确）→ 通过校验。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "客户5678 · 139****5678"},
                "sub_title_text": "动机：代步\n偏好：空间\n抗性：价格",
                "horizontal_content_list": [{"keyname": "整体标签", "value": "务实"}],
                "jump_list": [{"type": 1, "url": self.data["update_url"], "title": "查看完整画像"}],
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertTrue(V._sanitize_profile(draft, self.data))

    def test_button_interaction_draft_rejected(self):
        """button_interaction 草稿（旧格式）→ 画像卡拒绝 → 兜底 text_notice。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "button_interaction",
                "main_title": {"title": "test"},
                "button_list": [{"text": "换一个", "key": "restart"}],
            },
        }
        self.assertFalse(V._sanitize_profile(draft, self.data))

    def test_hallucinated_jump_url_rejected(self):
        """jump_list url != update_url → 幻觉 → 拒绝。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
                "jump_list": [{"type": 1, "url": "http://evil.com", "title": "查看完整画像"}],
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertFalse(V._sanitize_profile(draft, self.data))

    def test_missing_card_action_injected(self):
        """缺 card_action → 注入（不兜底）。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
            },
        }
        result = V._sanitize_profile(draft, self.data)
        self.assertTrue(result)
        self.assertEqual(draft["template_card"]["card_action"]["url"], self.data["update_url"])

    def test_button_list_stripped(self):
        """text_notice 草稿带 button_list → 删除。"""
        import validate_card as V
        draft = {
            "msgtype": "template_card",
            "template_card": {
                "card_type": "text_notice",
                "main_title": {"title": "test"},
                "button_list": [{"text": "多余", "key": "restart"}],
                "card_action": {"type": 1, "url": self.data["update_url"]},
            },
        }
        self.assertTrue(V._sanitize_profile(draft, self.data))
        self.assertNotIn("button_list", draft["template_card"])
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd assets/skills/automotive/customer-profile-update/scripts && python3 -m unittest tests.test_validate_card.TestSanitizeProfileTextNotice -v
```
Expected: FAIL（_sanitize_profile 仍要求 button_interaction）

- [ ] **Step 3: 实现 _sanitize_profile text_notice**

替换 `validate_card.py` 的 `_sanitize_profile` 函数（第 107-152 行）为：

```python
def _sanitize_profile(card, data):
    """画像卡校验+消毒（text_notice）。返回 True 可输出，False 须兜底。

    text_notice 结构：jump_list + card_action（必填）做「查看完整画像」跳转，
    无 button_list、无 task_id。url 幻觉 → 兜底；缺 card_action → 注入。
    """
    tc = card.get("template_card")
    if not isinstance(tc, dict) or tc.get("card_type") != "text_notice":
        return False

    update_url = data.get("update_url", "")

    # jump_list url 校验（每个 type:1 url 必须 == update_url）
    for item in tc.get("jump_list", []) or []:
        if isinstance(item, dict) and item.get("type") == 1 and item.get("url"):
            if item["url"] != update_url:
                return False  # 幻觉 url

    # card_action（text_notice 必填）：缺失→注入；幻觉 url→兜底
    ca = tc.get("card_action")
    if not isinstance(ca, dict) or ca.get("type") != 1:
        if update_url:
            tc["card_action"] = {"type": 1, "url": update_url}
        else:
            tc["card_action"] = {"type": 1, "url": "https://work.weixin.qq.com"}
    elif ca.get("url") and update_url and ca["url"] != update_url:
        return False  # 幻觉 url

    # 删 button_list（text_notice 无按钮）+ task_id（不需要）
    tc.pop("button_list", None)
    tc.pop("task_id", None)

    # hcl 截断 + 删残留 type:1 url 行（"更新画像"残留）
    hcl = tc.get("horizontal_content_list")
    if isinstance(hcl, list):
        if len(hcl) > MAX_HCL:
            hcl = hcl[:MAX_HCL]
        for item in hcl:
            if isinstance(item, dict):
                if isinstance(item.get("keyname"), str) and len(item["keyname"]) > MAX_KEYNAME:
                    item["keyname"] = item["keyname"][:MAX_KEYNAME]
                if isinstance(item.get("value"), str) and len(item["value"]) > MAX_VALUE:
                    item["value"] = item["value"][:MAX_VALUE]
                if item.get("type") == 1:
                    item.pop("type", None)
                    item.pop("url", None)
        tc["horizontal_content_list"] = hcl
    if not tc.get("main_title"):
        tc["main_title"] = {"title": "客户画像"}
    card["template_card"] = tc
    return True
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd assets/skills/automotive/customer-profile-update/scripts && python3 -m unittest tests.test_validate_card.TestSanitizeProfileTextNotice -v
```
Expected: 5 tests PASS

- [ ] **Step 5: 跑全量 validate_card 测试确认无回归**

```bash
cd assets/skills/automotive/customer-profile-update/scripts && python3 -m unittest tests.test_validate_card -v
```
Expected: ALL PASS（选择卡 button_interaction 测试不受影响）

- [ ] **Step 6: 提交**

```bash
cd /home/ubuntu/union_agent
git add assets/skills/automotive/customer-profile-update/scripts/validate_card.py assets/skills/automotive/customer-profile-update/scripts/tests/test_validate_card.py
git commit -m "feat(skill): validate_card 画像卡放行 text_notice（校验 jump_list/card_action url，缺 card_action 注入）

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 3: SKILL.md 画像卡指令改 text_notice

**Files:**
- Modify: `assets/skills/automotive/customer-profile-update/SKILL.md`（步骤5 画像卡段）

**Interfaces:**
- Consumes: Task 1-2 的 text_notice 卡片结构
- Produces: AI 遵循的画像卡建卡指令（text_notice）

- [ ] **Step 1: 更新 SKILL.md 步骤5 画像卡指令**

找到步骤5「### 步骤 5：AI 手写画像卡 + 校验」段，将画像卡字段取舍优先级 + button_interaction 相关指令替换为 text_notice 指令。关键改动：

1. 把「手写 `button_interaction` 画像卡 JSON 草稿」→「手写 `text_notice` 画像卡 JSON 草稿」
2. 删除 `button_list` / 「换一个」/ `task_id` 相关指令
3. 删除「更新画像」`horizontal_content_list` type:1 url 行指令
4. 加 `jump_list`（「查看完整画像」→ update_url）+ `card_action`（→ update_url，必填）指令
5. sub_title_text 改 `\n` 换行（动机/偏好/抗性各一行）
6. 参考 tdr 试驾报告卡片的 text_notice 样式

具体替换内容（步骤5 的「画像卡字段取舍优先级」段）：

```markdown
**画像卡字段（text_notice，参考试驾报告卡片样式）：**
1. `main_title.title` = 客户姓名 + 脱敏手机号（如 `客户5678 · 139****5678`）
2. `main_title.desc` = deal_level + personality_summary（如 `A级意向 · 务实家用型`，截断 20 字）
3. `sub_title_text` = 动机/偏好/抗性 **`\n` 换行**（如 `动机：家庭代步\n偏好：空间/油耗\n抗性：价格`，缺项跳过该行，≤112 字）
4. `horizontal_content_list`（≤5 项，无 url 行）：整体标签 / 意向车型 / 预算区间 / 购买阶段 / 突破策略
5. `jump_list` = `[{"type":1,"url":<update_url>,"title":"查看完整画像"}]`（url 必须 == stdin 的 update_url）
6. `card_action` = `{"type":1,"url":<update_url>}`（必填，url == update_url）
7. **无 `button_list`（无「换一个」）、无 `task_id`、无「更新画像」hcl 行**

`button_interaction` 的 `emphasis_content` / `button_list` 约束不再适用。text_notice 不支持按钮交互，换客户靠重新输入检索。
```

同时更新 SKILL.md 中其他引用「button_interaction 画像卡」/「换一个」/「更新画像」的地方（如 Gotchas #8「更新画像用 horizontal_content_list type:1 url」→ 改为「查看完整画像用 jump_list」；Gotchas #9「button_interaction 不支持 emphasis_content」→ 删除或改为 text_notice 说明）。

- [ ] **Step 2: 跑全量测试确认无回归**

```bash
cd assets/skills/automotive/customer-profile-update/scripts && python3 -m unittest discover -s tests -p "test_*.py"
```
Expected: ALL PASS

- [ ] **Step 3: 提交**

```bash
cd /home/ubuntu/union_agent
git add assets/skills/automotive/customer-profile-update/SKILL.md
git commit -m "docs(skill): SKILL.md 画像卡指令改 text_notice（去换一个/更新画像，加查看完整画像+\n sub_title）

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 4: references + manifest 同步

**Files:**
- Modify: `assets/skills/automotive/customer-profile-update/references/card-protocol.md`
- Modify: `assets/skills/automotive/customer-profile-update/references/api-spec.md`
- Modify: `assets/skills/automotive/customer-profile-update/manifest.json`

- [ ] **Step 1: 更新 card-protocol.md 画像卡段**

找到画像卡示例（约第 80-90 行），将 button_interaction 示例替换为 text_notice 示例：

```markdown
画像卡（text_notice）：
\`\`\`json
{
  "msgtype": "template_card",
  "template_card": {
    "card_type": "text_notice",
    "source": {"desc": "客户画像"},
    "main_title": {"title": "客户5678 · 139****5678", "desc": "A级意向 · 务实家用型"},
    "sub_title_text": "动机：家庭代步+安全\n偏好：空间/油耗\n抗性：价格/品牌力",
    "horizontal_content_list": [
      {"keyname": "整体标签", "value": "务实家用型"},
      {"keyname": "意向车型", "value": "追光"}
    ],
    "jump_list": [{"type": 1, "url": "<update_url>", "title": "查看完整画像"}],
    "card_action": {"type": 1, "url": "<update_url>"}
  }
}
\`\`\`

`horizontal_content_list`（≤5项）塞关键画像字段，动机/偏好/抗性放 `sub_title_text`（\n 换行）。
跳转用 `jump_list`「查看完整画像」+ `card_action`（必填）。无 button_list、无 task_id。
```

同时更新第 40 行「本 Skill 当前只用 button_interaction」→「画像卡用 text_notice，选择卡用 button_interaction」。

- [ ] **Step 2: 更新 api-spec.md 画像卡示例**

找到画像卡输出示例（约第 200 行），将 button_interaction 示例同步为 text_notice（同上结构）。

- [ ] **Step 3: bump manifest 版本**

`manifest.json` 的 `"version": "2.2.0"` → `"version": "2.3.0"`

- [ ] **Step 4: 跑全量测试**

```bash
cd assets/skills/automotive/customer-profile-update/scripts && python3 -m unittest discover -s tests -p "test_*.py"
```
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
cd /home/ubuntu/union_agent
git add assets/skills/automotive/customer-profile-update/references/card-protocol.md assets/skills/automotive/customer-profile-update/references/api-spec.md assets/skills/automotive/customer-profile-update/manifest.json
git commit -m "docs(skill): references + manifest 同步 text_notice 画像卡（v2.3.0）

via [HAPI](https://hapi.run)

Co-Authored-By: HAPI <noreply@hapi.run>"
```

---

### Task 5: \n 换行验证 + 本地冒烟

**Files:** 无新改动（验证 + 部署）

- [ ] **Step 1: 本地冒烟 build_profile_card + validate_card**

```bash
cd assets/skills/automotive/customer-profile-update/scripts
python3 -c "
from build_card import build_profile_card
from validate_card import _sanitize_profile
import json
data = {'ok':True,'has_profile':True,'fields':{'deal_level':'A','overall_tag':'务实','motivations':'代步','preferences':'空间','resistances':'价格'},'phone':'13912345678','customer_name':'客户5678','update_url':'http://example.com/p'}
card = build_profile_card(data)
print('build:', json.dumps(card, ensure_ascii=False, indent=2))
print('validate:', _sanitize_profile(card, data))
"
```
Expected: card_type text_notice + validate True

- [ ] **Step 2: k3s 热补 skill + 发测试卡验证 \n 渲染**

```bash
# 热补到集群（tar 管道）
tar czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='secrets.enc' -C /home/ubuntu/union_agent/assets/skills/automotive/customer-profile-update . | ssh ua-cloud 'kubectl -n unionagents exec -i engine-hermes-adf57637-8679bd4846-25x8t -c engine -- tar xzf - --no-same-owner -C /opt/data/skills/794e042f-ee92-4478-96af-62dcf1a6abcb/customer-profile-update/'
```

然后在企微发一条画像查询，确认：
- 卡片是 text_notice 样式（无按钮）
- sub_title_text 的 \n 是否渲染换行
- 「查看完整画像」跳转是否正常

- [ ] **Step 3: \n 不渲染则回退**

若 \n 不渲染换行，修改 `build_card.py` 的 `sub_title` 从 `"\n".join(parts)` 改为 `" · ".join(parts)`，SKILL.md 同步，重新热补。

- [ ] **Step 4: 确认后推送到 origin/develop**

```bash
cd /home/ubuntu/union_agent
git push origin develop
```

---

## Self-Review

**1. Spec coverage:**
- 需求1（减时延）：text_notice 更简单 → Task 1+3 ✓
- 需求2（text_notice + 去换一个/更新画像 + 查看完整画像）：Task 1（build）+ Task 2（validate）+ Task 3（SKILL.md）✓
- 需求3（\n sub_title）：Task 1（build \n）+ Task 5（验证 \n 渲染 + 回退）✓
- 澄清1（update_url）：Task 1（jump_list/card_action url = update_url）✓
- 澄清2（所有画像卡）：Task 1-2（build/validate 全改）✓
- 澄清3（\n 带回退）：Task 5 Step 3 ✓

**2. Placeholder scan:** 无 TBD/TODO。所有步骤含完整代码。✓

**3. Type consistency:** `build_profile_card(data)` → text_notice dict；`_sanitize_profile(card, data)` → bool。Task 1 产出的 text_notice 结构 = Task 2 校验的结构 = Task 3 SKILL.md 指令的结构。✓
