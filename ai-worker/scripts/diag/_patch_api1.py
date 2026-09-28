# -*- coding: utf-8 -*-
"""Stage-1 patch: add GET /agent/decisions/{decision_id} to api/v1/agent.py."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\backend\app\api\v1\agent.py"

s = io.open(PATH, encoding="utf-8").read()

OLD_IMPORT = """from app.services.agent.experiments import (
    build_experiment_proposal,
    active_experiment,
    serialize_experiment,
    list_experiments,
    start_experiment,
    finish_experiment,
    cancel_experiment,
    variant_history,
    EXPERIMENT_VERSION,
)"""
NEW_IMPORT = """from app.services.agent.experiments import (
    build_experiment_proposal,
    active_experiment,
    serialize_experiment,
    list_experiments,
    start_experiment,
    finish_experiment,
    cancel_experiment,
    get_decision,
    variant_history,
    EXPERIMENT_VERSION,
)"""
assert s.count(OLD_IMPORT) == 1
s = s.replace(OLD_IMPORT, NEW_IMPORT)

ANCHOR = '''@router.get("/context")
def context(user=Depends(current_user), db: Session = Depends(get_db)):
    return read_context(db, user)'''
NEW_ENDPOINT = '''@router.get("/decisions/{decision_id}")
def decision_ledger(
    decision_id: str,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    """Read model of one confirmed decision: signal, evidence, proposal,
    progress and review joined by decision_id (plan §5). The id is validated
    against the current user; unknown or foreign ids return 404 so the ledger
    is not enumerable."""
    if len(decision_id) < 4 or len(decision_id) > 64:
        raise HTTPException(status_code=404, detail="决策不存在")
    result = get_decision(db, user.id, decision_id)
    if result is None:
        raise HTTPException(status_code=404, detail="决策不存在")
    return result


@router.get("/context")
def context(user=Depends(current_user), db: Session = Depends(get_db)):
    return read_context(db, user)'''
assert s.count(ANCHOR) == 1
s = s.replace(ANCHOR, NEW_ENDPOINT)

io.open(PATH, "w", encoding="utf-8", newline="").write(s)
print("OK decisions endpoint added")
