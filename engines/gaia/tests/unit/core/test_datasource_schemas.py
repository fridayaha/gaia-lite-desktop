"""Schema tests for SyncTaskCreate.api_name naming pattern.

SyncTask api_name is an ops resource identifier (snake_case), aligned with
Dataset api_name (naming.DATASET_API_NAME_PATTERN), NOT a business camelCase
identifier. This was previously enforced as PROPERTY_API_NAME_PATTERN
(`^[a-z][a-zA-Z0-9]{0,99}$`, no underscores), which rejected legitimate
user-facing names like ``xiaoling_sync_model_instance``.
"""

import pytest
from pydantic import ValidationError

from ontology.core.schemas.datasource import SyncTaskCreate


def _base_kwargs(**overrides: object) -> dict:
    kwargs: dict = {
        "api_name": "xiaoling_sync_model_instance",
        "target_dataset_api_name": "model_instance_raw",
        "source_config": {"table": "model_instance"},
        "sync_mode": "full_snapshot",
        "transaction_type": "snapshot",
    }
    kwargs.update(overrides)
    return kwargs


class TestSyncTaskApiNamePattern:
    """SyncTask api_name must follow snake_case (DATASET_API_NAME_PATTERN)."""

    @pytest.mark.parametrize(
        "api_name",
        [
            "sync_orders",  # canonical snake_case
            "xiaoling_sync_model_instance",  # the regression case from the bug report
            "a",  # single char (min length)
            "t1",  # letter then digit
            "model_instance_raw1",  # trailing digit after underscore word
        ],
    )
    def test_valid_snake_case_accepted(self, api_name: str):
        task = SyncTaskCreate(**_base_kwargs(api_name=api_name))
        assert task.api_name == api_name

    @pytest.mark.parametrize(
        "api_name",
        [
            "syncOrders",  # camelCase — no longer accepted
            "SyncOrders",  # PascalCase
            "dealershipSync",  # legacy benchmark name — no longer accepted
            "_leading_underscore",  # leading underscore
            "1starts_with_digit",  # leading digit
            "has-dash",  # dash not allowed
            "has space",  # space not allowed
            "UPPER_CASE",  # uppercase letters not allowed
            "",  # empty
        ],
    )
    def test_invalid_names_rejected(self, api_name: str):
        with pytest.raises(ValidationError) as exc_info:
            SyncTaskCreate(**_base_kwargs(api_name=api_name))
        # The error must be on the api_name field, pattern mismatch.
        locs = [err["loc"] for err in exc_info.value.errors()]
        assert ("api_name",) in locs
