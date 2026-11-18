"""Pending time groups depend on selected observations, never unrelated rows."""
import json

from app import funds_projection as funds
from test_funds_projection import flow


def pending_case(database, make_case):
    cid, other = make_case("Synthetic pending groups"), make_case("Synthetic foreign pending group")
    for number in range(1, 9):
        flow(database, cid, number)
    flow(database, cid, 10)
    flow(database, other, 9)
    for number in (1, 2, 3, 4, 8, 9):
        database.x("insert into event_members(case_id,flow_id,status,reason) values (?,?,'unknown','time_basis_incomparable')",
                   [other if number == 9 else cid, number])
    database.x("insert into event_members(case_id,flow_id,status,reason) values (?,6,'confirmed','synthetic')", [cid])
    database.x("insert into event_members(case_id,flow_id,status,reason) values (?,7,'unknown','synthetic other reason')", [cid])
    for record, number, reference in [(1, 1, 'SYN-B'), (2, 2, 'SYN-B'), (3, 3, 'SYN-C'),
                                      (5, 5, 'SYN-B'), (6, 6, 'SYN-B'), (7, 7, 'SYN-B'),
                                      (8, 8, 'SYN-A'), (18, 8, 'SYN-B'), (9, 9, 'SYN-B'), (10, 10, 'SYN-Z')]:
        database.x("""insert into event_observation_cache(case_id,source_id,record_no,reference_key,flow_id,
            effective,source_typed,typed_accounts,evidence) values (?,'SYN-GROUP',?,?,?,true,false,false,'{}')""",
                   [other if number == 9 else cid, record, reference, number])
    return cid


def test_pending_reference_counts_keep_case_selection_and_null_semantics(database, make_case):
    cid = pending_case(database, make_case)
    source = "select f.* from flows f cross join range(2) copies where case_id=?"
    rows = database.q_raw(funds.cte(source) +
                         "select id,fact_time_pending from selected_observations order by id", [cid])
    assert rows == [{'id': number, 'fact_time_pending': value} for number, value in
                    [(1, True), (2, True), (3, False), (4, True), (5, None), (6, False), (7, False), (8, False), (10, False)]]
    subset = source + " and id in (1,3,4,8)"
    rows = database.q_raw(funds.cte(subset) +
                         "select id,fact_time_pending from selected_observations order by id", [cid])
    assert rows == [{'id': number, 'fact_time_pending': value} for number, value in
                    [(1, False), (3, False), (4, True), (8, False)]]


def test_pending_counts_do_not_window_the_unrelated_null_reference_partition(database, make_case):
    cid = pending_case(database, make_case)
    query = funds.summary_query("select * from flows where case_id=?")
    plan = json.loads(database.con().execute("explain (format json) " + query, [cid]).fetchone()[1])

    def windows(nodes):
        for node in nodes:
            if node['name'] == 'WINDOW':
                yield node.get('extra_info', {}).get('Projections', '')
            yield from windows(node.get('children', []))

    # The profiled failure spent most query CPU evaluating this filtered
    # window for observations without any comparable reference. Counts must
    # be computed by relevant group, retaining the public flags above.
    assert not any('reference_key' in str(projection) for projection in windows(plan))
