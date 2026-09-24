"""Temporary helper: render alembic head migration as full MySQL SQL.

Monkey-patches MySQLCompiler.render_literal_value so untyped literal
bindparams (NULL type) can be rendered as inline literals, which the
stock offline renderer refuses to do. Run from the backend/ directory.
"""
import sys

from sqlalchemy.dialects.mysql import base as mysql_base

_orig_render = mysql_base.MySQLCompiler.render_literal_value


def _patched_render(self, value, type_):
    try:
        return _orig_render(self, value, type_)
    except Exception:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return repr(value)
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"


mysql_base.MySQLCompiler.render_literal_value = _patched_render

from alembic.config import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(argv=["upgrade", "head", "--sql"]))
