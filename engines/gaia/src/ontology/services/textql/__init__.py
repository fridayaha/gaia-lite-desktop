"""TextQL subpackage — ontology-driven natural-language query pipeline.

See ADR-012 for the full design. Phase 1 components:
- intent_parser: Step 1 — NL → QueryIR (LLM structured output)
- semantic_recall: Step 2 engine A — exact-match recall from ontology metadata
- schema_provider: loads ontology schema for the compiler
- schema_injector: Step 3 — deterministic schema injection into LLM context
- sql_compiler: Step 4 path B — logical SQL → physical Doris/Trino SQL

Note: orchestrator (Step 1-3 wiring) is imported lazily by routes/ai.py
to avoid a circular import (orchestrator type-annotates Container).
"""

from ontology.services.textql.intent_parser import parse_intent
from ontology.services.textql.schema_injector import SchemaInjector
from ontology.services.textql.schema_provider import MetaStoreSchemaProvider
from ontology.services.textql.semantic_recall import SemanticRecaller
from ontology.services.textql.sql_compiler import (
    OntologySchemaProvider,
    OntologySqlCompiler,
)

__all__ = [
    "MetaStoreSchemaProvider",
    "OntologySchemaProvider",
    "OntologySqlCompiler",
    "SchemaInjector",
    "SemanticRecaller",
    "parse_intent",
]
