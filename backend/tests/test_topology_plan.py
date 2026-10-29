from app.graph import constants as C
from app.ingestion.build_topology import plan_topology


def test_distribution_is_12_or_13_per_line_in_id_order() -> None:
    plan = plan_topology(list(range(1, 101)))
    sizes = [len(plan.line_machines[lid]) for lid, _ in C.LINES]
    assert sum(sizes) == 100
    assert set(sizes) == {12, 13}
    assert plan.line_machines["A1"][0] == "M001"
    assert plan.line_machines["B4"][-1] == "M100"
    flat = [m for lid, _ in C.LINES for m in plan.line_machines[lid]]
    assert flat == sorted(flat)


def test_plan_is_deterministic() -> None:
    a = plan_topology(list(range(1, 101)))
    b = plan_topology(list(range(100, 0, -1)))
    assert a == b


def test_feeds_chain_plus_three_cross_line_links() -> None:
    plan = plan_topology(list(range(1, 101)))
    in_line = sum(len(m) - 1 for m in plan.line_machines.values())
    assert len(plan.feeds) == in_line + 3
    assert (plan.line_machines["A2"][-1], plan.line_machines["A3"][0]) in plan.feeds


def test_feeds_graph_is_acyclic() -> None:
    plan = plan_topology(list(range(1, 101)))
    order = {m: i for i, m in enumerate(sorted(plan.placement))}
    assert all(order[a] < order[b] for a, b in plan.feeds)


def test_ten_technicians() -> None:
    assert len(plan_topology([1, 2, 3]).technicians) == 10
