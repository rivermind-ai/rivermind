"""Reusable MemoryStore contract test suite.

Any concrete MemoryStore implementation can assert compliance by
subclassing ``MemoryStoreContractTests`` and providing a ``store`` fixture
that yields a fresh, empty instance::

    class TestMyAdapterContract(MemoryStoreContractTests):
        @pytest.fixture
        def store(self, tmp_db_path):
            s = MyAdapter(tmp_db_path)
            try:
                yield s
            finally:
                s.close()

The suite covers every public method on the :class:`MemoryStore` Protocol.
Topic search uses plain single-word queries so backends with different
full-text syntaxes (FTS5, tsvector, vector similarity) can all satisfy
the contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rivermind.core.interfaces import MemoryStore
from rivermind.core.models import Kind, Narrative, Observation, State

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class MemoryStoreContractTests:
    """Inherit this class in an adapter test module and provide a ``store``
    fixture. Every test below runs against that store.
    """

    # ---- Protocol conformance ---------------------------------------------

    def test_store_satisfies_protocol(self, store: MemoryStore) -> None:
        assert isinstance(store, MemoryStore)

    # ---- save_observation / get_observations ------------------------------

    def test_save_and_get_observation_roundtrip(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        obs = Observation(
            id="obs-1",
            content="user works at Acme",
            kind=Kind.FACT,
            subject="user",
            attribute="employer",
            value={"company": "Acme", "since": 2026},
            observed_at=t(0),
            source_model="claude-opus",
            session_id="sess-1",
        )
        store.save_observation(obs)
        got = store.get_observations(t(-60), t(60))
        assert got == [obs]

    def test_get_observations_returns_empty_when_no_data(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        assert store.get_observations(t(-60), t(60)) == []

    def test_get_observations_filters_by_range(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        for i in [-120, 0, 120]:
            store.save_observation(
                Observation(
                    id=f"obs-{i}",
                    content=f"event {i}",
                    kind=Kind.EVENT,
                    observed_at=t(i),
                )
            )
        got = store.get_observations(t(-60), t(60))
        assert [o.id for o in got] == ["obs-0"]

    def test_get_observations_ordered_ascending(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        for i in [2, 0, 1]:
            store.save_observation(
                Observation(
                    id=f"obs-{i}",
                    content=f"event {i}",
                    kind=Kind.EVENT,
                    observed_at=t(i),
                )
            )
        got = store.get_observations(t(-60), t(60))
        assert [o.id for o in got] == ["obs-0", "obs-1", "obs-2"]

    def test_get_observations_topic_filter_hits(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        store.save_observation(
            Observation(
                id="obs-a",
                content="visited Acme HQ",
                kind=Kind.EVENT,
                observed_at=t(0),
            )
        )
        store.save_observation(
            Observation(
                id="obs-b",
                content="lunch with a friend",
                kind=Kind.EVENT,
                observed_at=t(1),
            )
        )
        hits = store.get_observations(t(-60), t(60), topic="Acme")
        assert [o.id for o in hits] == ["obs-a"]

    def test_get_observations_topic_no_match_returns_empty(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        store.save_observation(
            Observation(
                id="obs-c",
                content="lunch",
                kind=Kind.EVENT,
                observed_at=t(0),
            )
        )
        assert store.get_observations(t(-60), t(60), topic="bicycle") == []

    def test_get_observations_limit_caps_result_count(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        for i in range(5):
            store.save_observation(
                Observation(
                    id=f"obs-{i}",
                    content=f"event {i}",
                    kind=Kind.EVENT,
                    observed_at=t(i),
                )
            )
        got = store.get_observations(t(-60), t(60), limit=3)
        assert [o.id for o in got] == ["obs-0", "obs-1", "obs-2"]

    def test_get_observations_excludes_superseded_by_default(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        store.save_observation(
            Observation(
                id="obs-newer",
                content="joined Acme",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Acme",
                observed_at=t(60),
            )
        )
        store.save_observation(
            Observation(
                id="obs-older",
                content="joined Globex",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Globex",
                observed_at=t(0),
                superseded_by="obs-newer",
            )
        )
        default = store.get_observations(t(-60), t(120))
        assert {o.id for o in default} == {"obs-newer"}

    def test_get_observations_include_superseded_returns_all(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        store.save_observation(
            Observation(
                id="obs-newer",
                content="joined Acme",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Acme",
                observed_at=t(60),
            )
        )
        store.save_observation(
            Observation(
                id="obs-older",
                content="joined Globex",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Globex",
                observed_at=t(0),
                superseded_by="obs-newer",
            )
        )
        full = store.get_observations(t(-60), t(120), include_superseded=True)
        assert {o.id for o in full} == {"obs-older", "obs-newer"}

    # ---- upsert_state / get_state -----------------------------------------

    def test_upsert_state_inserts_new_row(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        store.save_observation(
            Observation(
                id="obs-1",
                content="joined Acme",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Acme",
                observed_at=t(0),
            )
        )
        store.upsert_state(
            State(
                subject="user",
                attribute="employer",
                current_value="Acme",
                current_since=t(0),
                source_observation="obs-1",
            )
        )
        got = store.get_state("user", "employer")
        assert got is not None
        assert got.current_value == "Acme"

    def test_upsert_state_updates_on_newer_current_since(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        for i, (value, offset) in enumerate([("Globex", 0), ("Acme", 3600)]):
            store.save_observation(
                Observation(
                    id=f"obs-{i}",
                    content=f"joined {value}",
                    kind=Kind.FACT,
                    subject="user",
                    attribute="employer",
                    value=value,
                    observed_at=t(offset),
                )
            )
            store.upsert_state(
                State(
                    subject="user",
                    attribute="employer",
                    current_value=value,
                    current_since=t(offset),
                    source_observation=f"obs-{i}",
                )
            )
        got = store.get_state("user", "employer")
        assert got is not None
        assert got.current_value == "Acme"
        assert got.source_observation == "obs-1"

    def test_upsert_state_drops_stale_write(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        store.save_observation(
            Observation(
                id="obs-old",
                content="joined Globex",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Globex",
                observed_at=t(0),
            )
        )
        store.save_observation(
            Observation(
                id="obs-new",
                content="joined Acme",
                kind=Kind.FACT,
                subject="user",
                attribute="employer",
                value="Acme",
                observed_at=t(3600),
            )
        )
        store.upsert_state(
            State(
                subject="user",
                attribute="employer",
                current_value="Acme",
                current_since=t(3600),
                source_observation="obs-new",
            )
        )
        store.upsert_state(
            State(
                subject="user",
                attribute="employer",
                current_value="Globex",
                current_since=t(0),
                source_observation="obs-old",
            )
        )
        got = store.get_state("user", "employer")
        assert got is not None
        assert got.current_value == "Acme"
        assert got.source_observation == "obs-new"

    def test_get_state_returns_none_on_miss(self, store: MemoryStore) -> None:
        assert store.get_state("nobody", "nothing") is None

    # ---- list_states ------------------------------------------------------

    def _seed_states(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        rows = [
            ("user", "employer", "Acme"),
            ("user", "role", "staff engineer"),
            ("team", "employer", "Globex"),
        ]
        for i, (subject, attribute, value) in enumerate(rows):
            store.save_observation(
                Observation(
                    id=f"obs-seed-{i}",
                    content=f"{subject} {attribute} {value}",
                    kind=Kind.FACT,
                    subject=subject,
                    attribute=attribute,
                    value=value,
                    observed_at=t(i),
                )
            )
            store.upsert_state(
                State(
                    subject=subject,
                    attribute=attribute,
                    current_value=value,
                    current_since=t(i),
                    source_observation=f"obs-seed-{i}",
                )
            )

    def test_list_states_no_filter_returns_all(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        self._seed_states(store, t)
        rows = store.list_states()
        assert {(s.subject, s.attribute) for s in rows} == {
            ("user", "employer"),
            ("user", "role"),
            ("team", "employer"),
        }

    def test_list_states_filter_by_subject(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        self._seed_states(store, t)
        rows = store.list_states(subject="user")
        assert {s.attribute for s in rows} == {"employer", "role"}

    def test_list_states_filter_by_attribute(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        self._seed_states(store, t)
        rows = store.list_states(attribute="employer")
        assert {s.subject for s in rows} == {"user", "team"}

    def test_list_states_filter_by_both(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        self._seed_states(store, t)
        rows = store.list_states(subject="user", attribute="employer")
        assert len(rows) == 1
        assert rows[0].current_value == "Acme"

    def test_list_states_empty_on_no_match(self, store: MemoryStore) -> None:
        assert store.list_states(subject="nobody") == []

    # ---- save_narrative / get_narratives ----------------------------------

    def test_save_narrative_roundtrips_source_observations(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        for i in range(2):
            store.save_observation(
                Observation(
                    id=f"obs-{i}",
                    content=f"event {i}",
                    kind=Kind.EVENT,
                    observed_at=t(i),
                )
            )
        n = Narrative(
            id="nar-1",
            content="weekly summary",
            topic="career",
            period_start=t(0),
            period_end=t(3600),
            source_observations=["obs-0", "obs-1"],
        )
        store.save_narrative(n)
        got = store.get_narratives(t(-60), t(4000))
        assert len(got) == 1
        assert got[0].source_observations == ["obs-0", "obs-1"]

    def test_get_narratives_window_overlap(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        narratives = [
            ("nar-before", t(-7200), t(-3600)),
            ("nar-overlap-left", t(-3600), t(30)),
            ("nar-inside", t(10), t(50)),
            ("nar-after", t(3600), t(7200)),
        ]
        for nid, start, end in narratives:
            store.save_narrative(
                Narrative(
                    id=nid,
                    content="x",
                    period_start=start,
                    period_end=end,
                    source_observations=[],
                )
            )
        got = store.get_narratives(t(0), t(60))
        assert {n.id for n in got} == {"nar-overlap-left", "nar-inside"}

    def test_get_narratives_topic_is_exact_match(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        store.save_narrative(
            Narrative(
                id="nar-1",
                content="Acme notes and project review",
                topic="career",
                period_start=t(0),
                period_end=t(60),
                source_observations=[],
            )
        )
        assert store.get_narratives(t(-60), t(120), topic="career") != []
        # "Acme" is in content but not in topic; topic filter should miss
        assert store.get_narratives(t(-60), t(120), topic="Acme") == []

    # ---- schema_version ---------------------------------------------------

    def test_schema_version_returns_positive_int(self, store: MemoryStore) -> None:
        version = store.schema_version()
        assert isinstance(version, int)
        assert version > 0

    def test_get_narratives_ordered_by_generated_at_desc(
        self,
        store: MemoryStore,
        t: Callable[[int], datetime],
    ) -> None:
        for i in range(3):
            store.save_narrative(
                Narrative(
                    id=f"nar-{i}",
                    content="x",
                    period_start=t(0),
                    period_end=t(60),
                    source_observations=[],
                    generated_at=t(i),
                )
            )
        got = store.get_narratives(t(-60), t(120))
        assert [n.id for n in got] == ["nar-2", "nar-1", "nar-0"]
