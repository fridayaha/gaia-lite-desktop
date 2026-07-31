import sys
import os
import json

# Setup environment
sys.path.append("/home/xiaox/projects/union_agent/services/union-agent-hub/backend")
os.environ["DATABASE_URL"] = "sqlite:///./hub_smoke_dev.db"

from app.db.session import SessionLocal
from app.services.openapi_import_service import OpenAPIImportService

def debug_import():
    db = SessionLocal()
    svc = OpenAPIImportService(db)
    
    fixture_path = "/home/xiaox/projects/union_agent/services/union-agent-hub/backend/tests/fixtures/minimal_openapi.json"
    with open(fixture_path, "rb") as f:
        content = f.read()
    
    print(f"Importing {fixture_path}...")
    try:
        # We don't use try-except around import_from_spec to see the warnings inside the result if it didn't raise
        # But we know it raises ValueError if tool_count == 0.
        # Let's mock the log_event to avoid errors if it's not configured
        import app.services.openapi_import_service as ois
        ois.log_event = lambda *args, **kwargs: None
        
        # We can also wrap _create_tool to catch and print
        orig_create = svc._create_tool
        def mocked_create(*args, **kwargs):
            try:
                return orig_create(*args, **kwargs)
            except Exception as e:
                print(f"DEBUG: _create_tool failed: {e}")
                import traceback
                traceback.print_exc()
                raise
        svc._create_tool = mocked_create
        
        result = svc.import_from_spec(content, "spec.json")
        print("Success!")
        print(json.dumps(result, indent=2))
    except ValueError as e:
        print(f"Caught ValueError: {e}")
    except Exception as e:
        print(f"Caught Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    debug_import()
