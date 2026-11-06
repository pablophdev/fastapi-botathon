import os
from contextlib import contextmanager

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()


@contextmanager
def conexion(*, escritura=False):
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Falta DATABASE_URL. Copia .env.example a .env y configura Supabase.")

    with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as db:
        if not escritura:
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        db.execute("SET LOCAL search_path TO cada_hora")
        if escritura:
            # Serializa lotes y edición de parámetros entre instancias de la API.
            db.execute("SELECT pg_advisory_xact_lock(hashtext('cada_hora_escrituras'))")
        yield db


def verificar_base():
    with conexion() as db:
        db.execute("SELECT 1 FROM cada_hora.cargas LIMIT 1")
