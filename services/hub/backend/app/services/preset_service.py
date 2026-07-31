from sqlalchemy.orm import Session

from app.core.enums import HubItemStatus, HubItemType, HubItemVersionStatus, RiskLevel, SourceType
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion

PRESET_DEFS = [
    {
        "name": "招投标合规检查 Agent",
        "type": HubItemType.agent,
        "description": "自动审查招投标文件的合规性",
        "industry": "政府采购",
        "scenario": "合规检查",
        "version": "0.1.0",
        "manifest_json": {"agent_type": "compliance"},
        "input_schema": {"file": {"type": "string"}},
    },
    {
        "name": "文件系统 MCP 配置示例",
        "type": HubItemType.mcp,
        "description": "安全的文件系统操作服务",
        "industry": "DevOps",
        "scenario": "文件管理",
        "version": "0.1.0",
        "manifest_json": {"server": "filesystem", "paths": ["/data"]},
        "input_schema": {"operation": {"type": "string"}},
    },
    {
        "name": "长文档摘要 Skill",
        "type": HubItemType.skill,
        "description": "对长文档生成结构化摘要",
        "industry": "通用",
        "scenario": "文档处理",
        "version": "0.1.0",
        "manifest_json": {"skill_type": "summarization"},
        "input_schema": {"document": {"type": "string"}},
    },
    {
        "name": "PDF 文本抽取 Tool",
        "type": HubItemType.tool,
        "description": "从 PDF 中抽取出文本内容",
        "industry": "通用",
        "scenario": "文档处理",
        "version": "0.1.0",
        "manifest_json": {"tool_type": "pdf_extractor"},
        "input_schema": {"pdf_url": {"type": "string"}},
    },
]


class PresetService:
    def __init__(self, db: Session):
        self.db = db

    def init_presets(self) -> dict:
        created = 0
        skipped = 0
        items: list[HubItem] = []

        for preset in PRESET_DEFS:
            existing = (
                self.db.query(HubItem)
                .filter(
                    HubItem.name == preset["name"],
                    HubItem.type == preset["type"],
                )
                .first()
            )
            if existing is not None:
                skipped += 1
                items.append(existing)
                continue

            item = HubItem(
                name=preset["name"],
                type=preset["type"],
                description=preset["description"],
                industry=preset["industry"],
                scenario=preset["scenario"],
                source_type=SourceType.preset,
                status=HubItemStatus.draft,
                risk_level=RiskLevel.low,
                discoverable=True,
                allow_existing_references=True,
                force_disabled=False,
                organization_id="default",
                workspace_id="default",
                visibility_scope="workspace",
            )
            self.db.add(item)
            self.db.flush()

            version = HubItemVersion(
                hub_item_id=item.id,
                version=preset["version"],
                manifest_json=preset.get("manifest_json"),
                input_schema=preset.get("input_schema"),
                output_schema=preset.get("output_schema"),
                permission_json=preset.get("permission_json"),
                runtime_compatibility=preset.get("runtime_compatibility"),
                status=HubItemVersionStatus.draft,
                risk_level=RiskLevel.low,
                organization_id=item.organization_id,
                workspace_id=item.workspace_id,
            )
            self.db.add(version)

            created += 1
            items.append(item)

        self.db.commit()
        return {
            "created": created,
            "skipped": skipped,
            "items": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "type": item.type.value,
                    "source_type": item.source_type.value,
                    "status": item.status.value,
                }
                for item in items
            ],
        }
