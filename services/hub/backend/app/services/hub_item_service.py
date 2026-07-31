import uuid

from sqlalchemy.orm import Session, selectinload

from app.core.enums import HubItemStatus, RiskLevel, SourceType
from app.core.tenancy import (
    DEFAULT_ORGANIZATION_ID,
    DEFAULT_WORKSPACE_ID,
    normalize_visibility_scope,
    resolve_tenant_ids,
)

from app.policies.tenant_policy import apply_tenant_filter_to_items

from app.core.auth_context import AuthContext
from app.models.hub_item import HubItem
from app.schemas.hub_item import HubItemCreate, HubItemUpdate
from app.schemas.hub_item_list import HubItemListFilters
from app.services.exceptions import HubItemNotFoundError


class HubItemService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: HubItemCreate,
               organization_id: str | None = None,
               workspace_id: str | None = None) -> HubItem:
        org_id, ws_id = resolve_tenant_ids(organization_id, workspace_id)
        item = HubItem(
            name=data.name,
            type=data.type,
            description=data.description,
            industry=data.industry,
            scenario=data.scenario,
            category_id=data.category_id,
            source_type=data.source_type,
            status=HubItemStatus.draft,
            risk_level=data.risk_level,
            discoverable=True,
            allow_existing_references=True,
            force_disabled=False,
            featured=data.featured,
            organization_id=org_id,
            workspace_id=ws_id,
            visibility_scope=normalize_visibility_scope(data.visibility_scope),
            created_by=data.created_by,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def get_by_id(self, item_id: uuid.UUID) -> HubItem:
        item = (
            self.db.query(HubItem)
            .options(selectinload(HubItem.tags))
            .filter(HubItem.id == item_id)
            .first()
        )
        if item is None:
            raise HubItemNotFoundError(str(item_id))
        return item

    def list_with_total(
        self,
        filters: HubItemListFilters,
        skip: int = 0,
        limit: int = 20,
        organization_id: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[list[HubItem], int]:
        query = self.db.query(HubItem).options(selectinload(HubItem.tags))
        if workspace_id is not None:
            from app.core.auth_context import AuthContext
            ctx = AuthContext(
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
            query = apply_tenant_filter_to_items(query, ctx)

        if filters.type:
            query = query.filter(HubItem.type == filters.type)
        if filters.status:
            query = query.filter(HubItem.status == filters.status)
        if filters.risk_level:
            query = query.filter(HubItem.risk_level == filters.risk_level)
        if filters.source_type:
            query = query.filter(HubItem.source_type == filters.source_type)
        if filters.featured is not None:
            query = query.filter(HubItem.featured == filters.featured)
        if filters.keyword:
            kw = f"%{filters.keyword}%"
            query = query.filter(
                HubItem.name.ilike(kw) | HubItem.description.ilike(kw)
            )

        total = query.count()
        items = query.order_by(HubItem.created_at.desc()).offset(skip).limit(limit).all()
        return items, total

    def update(self, item_id: uuid.UUID, data: HubItemUpdate) -> HubItem:
        item = self.get_by_id(item_id)
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(item, key, value)
        self.db.commit()
        self.db.refresh(item)
        return item
