import os
from sqlalchemy import create_engine

# Load the database URL from the environment
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

