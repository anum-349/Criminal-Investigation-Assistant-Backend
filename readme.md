create .env with secrets 
JWT_SECRET = "cia-2026"
SQLALCHEMY_DATABASE_URL = "postgresql://postgres:password@localhost:5432/database_name" 
ADMIN_SECRET_CODE = "admin-secret-2026"

run in cmd:
mkdir sqlite_datas
python models.py
python seeds.py
python seed_users.py
python seed_cases.py