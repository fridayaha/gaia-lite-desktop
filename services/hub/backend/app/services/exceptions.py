class HubItemNotFoundError(Exception):
    def __init__(self, item_id: str):
        self.item_id = item_id
        super().__init__(f"HubItem not found: {item_id}")


class HubItemVersionNotFoundError(Exception):
    def __init__(self, version_id: str):
        self.version_id = version_id
        super().__init__(f"HubItemVersion not found: {version_id}")


class DuplicateVersionError(Exception):
    def __init__(self, item_id: str, version: str):
        self.item_id = item_id
        self.version = version
        super().__init__(f"Duplicate version '{version}' for item {item_id}")


class InvalidStateTransitionError(Exception):
    def __init__(self, from_status: str, to_status: str, entity: str):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Invalid state transition: {entity} cannot go from "
            f"'{from_status}' to '{to_status}'"
        )


class RollbackTargetInvalidError(Exception):
    def __init__(self, reason: str):
        super().__init__(f"Invalid rollback target: {reason}")


class ApprovalStateInvalidError(Exception):
    def __init__(self, current_status: str):
        self.current_status = current_status
        super().__init__(
            f"Version status '{current_status}' is not eligible for approval"
        )


class BlockingRiskApprovalError(Exception):
    def __init__(self, version_id: str):
        self.version_id = version_id
        super().__init__(
            f"Version {version_id} has blocking risk level and cannot be approved"
        )


class InvalidManifestError(Exception):
    def __init__(self, reason: str):
        super().__init__(f"Invalid manifest: {reason}")


class ZipSlipError(Exception):
    def __init__(self, path: str):
        super().__init__(f"Zip slip detected: {path}")


class UnsupportedFormatError(Exception):
    def __init__(self, filename: str):
        super().__init__(f"Unsupported file format: {filename}")


class RelationNotFoundError(Exception):
    def __init__(self, relation_id: str):
        self.relation_id = relation_id
        super().__init__(f"HubItemRelation not found: {relation_id}")


class DuplicateRelationError(Exception):
    def __init__(self, source_id: str, target_id: str, relation_type: str, relation_scope: str):
        super().__init__(
            f"Duplicate relation: source={source_id} target={target_id} "
            f"type={relation_type} scope={relation_scope}"
        )


class SelfRelationError(Exception):
    def __init__(self, item_id: str):
        super().__init__(f"Cannot create relation from item to itself: {item_id}")


class InvalidRelationTypeCombinationError(Exception):
    def __init__(self, source_type: str, target_type: str, relation_type: str):
        super().__init__(
            f"Invalid relation type combination: {source_type} --{relation_type}--> {target_type}"
        )


class RequiredDependencyUnavailableError(Exception):
    def __init__(self, target_item_id: str):
        self.target_item_id = target_item_id
        super().__init__("required dependency not available")


class VersionNotScannedError(Exception):
    def __init__(self, version_id: str):
        self.version_id = version_id
        super().__init__(f"版本尚未扫描，不能审批通过: {version_id}")


class BlockingRiskSubmitError(Exception):
    def __init__(self, version_id: str):
        self.version_id = version_id
        super().__init__("安全扫描发现 blocking 风险，不能提交审核")


class ApprovalPolicyDeniedError(Exception):
    def __init__(self, reason: str = "approval policy denied"):
        self.reason = reason
        super().__init__(reason)

