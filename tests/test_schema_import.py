from src.data.models import Base, Match, Team


def test_schema_symbols_exist() -> None:
    assert Base is not None
    assert Team.__tablename__ == "teams"
    assert Match.__tablename__ == "matches"
