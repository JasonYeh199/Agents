import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["OBJECT_STORE_PATH"] = ".data/test-objects"
os.environ["MODEL_PROVIDER"] = "deterministic"
