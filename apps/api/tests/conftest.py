import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["OBJECT_STORE_PATH"] = ".data/test-objects"
os.environ["MODEL_PROVIDER"] = "deterministic"
os.environ["OPENAI_API_KEY"] = ""
os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ADMIN_SESSION_SECRET"] = "test-session-signing-secret"
os.environ["ADMIN_COOKIE_SECURE"] = "false"
os.environ["FIXTURE_MODE"] = "true"
os.environ["SEC_USER_AGENT"] = "SignalForge tests test@example.com"
