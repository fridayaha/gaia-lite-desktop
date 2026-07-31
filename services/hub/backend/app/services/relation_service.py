import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.enums import HubItemType, RelationType
from app.models.hub_item import HubItem
from app.models.hub_item_relation import HubItemRelation
from app.schemas.hub_item_relation import RelationCreate
from app.services.exceptions import (
    DuplicateRelationError,
    HubItemNotFoundError,
    InvalidRelationTypeCombinationError,
    RelationNotFoundError,
    SelfRelationError,
)

_VALID_COMBINATIONS = {
    (HubItemType.agent, HubItemType.skill, RelationType.uses),
    (HubItemType.agent, HubItemType.tool, RelationType.invokes),
    (HubItemType.agent, HubItemType.mcp, RelationType.depends_on),
    (HubItemType.skill, HubItemType.tool, RelationType.invokes),
    (HubItemType.skill, HubItemType.mcp, RelationType.depends_on),
    (HubItemType.mcp, HubItemType.tool, RelationType.provides),
}


class RelationService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: RelationCreate) -> HubItemRelation:
        source_item = self.db.get(HubItem, data.source_item_id)
        if source_item is None:
            raise HubItemNotFoundError(str(data.source_item_id))

        target_item = self.db.get(HubItem, data.target_item_id)
        if target_item is None:
            raise HubItemNotFoundError(str(data.target_item_id))

        if data.source_item_id == data.target_item_id:
            raise SelfRelationError(str(data.source_item_id))

        combo = (source_item.type, target_item.type, data.relation_type)
        if combo not in _VALID_COMBINATIONS:
            raise InvalidRelationTypeCombinationError(
                str(source_item.type), str(target_item.type), str(data.relation_type)
            )

        existing = (
            self.db.query(HubItemRelation)
            .filter(
                HubItemRelation.source_item_id == data.source_item_id,
                HubItemRelation.target_item_id == data.target_item_id,
                HubItemRelation.relation_type == data.relation_type,
                HubItemRelation.relation_scope == data.relation_scope,
            )
            .first()
        )
        if existing is not None:
            raise DuplicateRelationError(
                str(data.source_item_id),
                str(data.target_item_id),
                str(data.relation_type),
                str(data.relation_scope),
            )

        relation = HubItemRelation(
            source_item_id=data.source_item_id,
            target_item_id=data.target_item_id,
            relation_type=data.relation_type,
            relation_scope=data.relation_scope,
            required=data.required,
            description=data.description,
            organization_id=source_item.organization_id,
            workspace_id=source_item.workspace_id,
            created_by=data.created_by,
        )
        self.db.add(relation)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise DuplicateRelationError(
                str(data.source_item_id),
                str(data.target_item_id),
                str(data.relation_type),
                str(data.relation_scope),
            )
        self.db.refresh(relation)

        return (
            self.db.query(HubItemRelation)
            .options(
                joinedload(HubItemRelation.source_item),
                joinedload(HubItemRelation.target_item),
            )
            .filter(HubItemRelation.id == relation.id)
            .one()
        )

    def get_by_id(self, relation_id: uuid.UUID) -> HubItemRelation:
        relation = (
            self.db.query(HubItemRelation)
            .options(
                joinedload(HubItemRelation.source_item),
                joinedload(HubItemRelation.target_item),
            )
            .filter(HubItemRelation.id == relation_id)
            .first()
        )
        if relation is None:
            raise RelationNotFoundError(str(relation_id))
        return relation

    def list_by_item(
        self, item_id: uuid.UUID
    ) -> tuple[list[HubItemRelation], list[HubItemRelation]]:
        item = self.db.get(HubItem, item_id)
        if item is None:
            raise HubItemNotFoundError(str(item_id))

        outgoing = (
            self.db.query(HubItemRelation)
            .options(
                joinedload(HubItemRelation.source_item),
                joinedload(HubItemRelation.target_item),
            )
            .filter(HubItemRelation.source_item_id == item_id)
            .all()
        )

        incoming = (
            self.db.query(HubItemRelation)
            .options(
                joinedload(HubItemRelation.source_item),
                joinedload(HubItemRelation.target_item),
            )
            .filter(HubItemRelation.target_item_id == item_id)
            .all()
        )

        return outgoing, incoming

    def delete(self, relation_id: uuid.UUID) -> None:
        relation = self.db.get(HubItemRelation, relation_id)
        if relation is None:
            raise RelationNotFoundError(str(relation_id))
        self.db.delete(relation)
        self.db.commit()
