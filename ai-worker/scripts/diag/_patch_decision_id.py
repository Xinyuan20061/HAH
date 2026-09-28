# -*- coding: utf-8 -*-
"""Apply decision_id column to AgentMicroExperiment model (idempotent patch)."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\models\models.py"

OLD = '''    __tablename__ = "agent_micro_experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    insight_code: Mapped[str] = mapped_column(String(60), index=True)'''

NEW = '''    __tablename__ = "agent_micro_experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Stable ledger key: one decision_id joins signal -> proposal -> confirmed
    # action -> progress -> review for a single experiment (plan \u00a75).
    decision_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, default="")
    insight_code: Mapped[str] = mapped_column(String(60), index=True)'''

s = io.open(PATH, encoding="utf-8").read()
count = s.count(OLD)
assert count == 1, "match count = %d" % count
s = s.replace(OLD, NEW)
io.open(PATH, "w", encoding="utf-8", newline="").write(s)
print("OK models.py updated")
