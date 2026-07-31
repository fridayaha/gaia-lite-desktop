import uuid

from sqlalchemy.orm import Session

from app.core.enums import HubItemStatus, HubItemVersionStatus, RiskLevel, EventType
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.models.lifecycle_event import LifecycleEvent
from app.models.scan_report import ScanReport


def create_item_db(db: Session, name: str, item_type: str) -> str:
    item = HubItem(
        id=uuid.uuid4(),
        name=name,
        type=item_type,
        status=HubItemStatus.draft,
    )
    db.add(item)
    db.commit()
    return str(item.id)


def create_version_db(db: Session, item_id: str, version: str) -> str:
    ver = HubItemVersion(
        id=uuid.uuid4(),
        hub_item_id=uuid.UUID(item_id),
        version=version,
        status=HubItemVersionStatus.draft,
    )
    db.add(ver)
    db.commit()
    return str(ver.id)


def set_version_status(db: Session, version_id: str, status: str):
    v = db.get(HubItemVersion, uuid.UUID(version_id))
    if v is not None:
        v.status = status
        db.commit()


def set_version_risk(db: Session, version_id: str, risk: str):
    v = db.get(HubItemVersion, uuid.UUID(version_id))
    if v is not None:
        v.risk_level = risk
        db.commit()


def submit_version(db: Session, version_id: str, item_id: str, operator: str):
    v = db.get(HubItemVersion, uuid.UUID(version_id))
    if v is not None:
        v.status = HubItemVersionStatus.pending_review
    event = LifecycleEvent(
        id=uuid.uuid4(),
        hub_item_id=uuid.UUID(item_id),
        hub_item_version_id=uuid.UUID(version_id),
        event_type=EventType.submitted,
        from_status="draft",
        to_status="pending_review",
        operator=operator,
    )
    db.add(event)
    db.commit()


def add_scan_report(db: Session, version_id: str):
    ver = db.get(HubItemVersion, uuid.UUID(version_id))
    item_id = ver.hub_item_id if ver else uuid.UUID(version_id)
    report = ScanReport(
        id=uuid.uuid4(),
        hub_item_id=item_id,
        hub_item_version_id=uuid.UUID(version_id),
        risk_level=RiskLevel.low,
    )
    db.add(report)
    db.commit()
