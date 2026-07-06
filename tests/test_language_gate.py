from app.services.language_gate import (
    TIER_BOILERPLATE,
    TIER_ENGLISH_FRIENDLY,
    TIER_REQUIRED_C1,
    TIER_UNSPECIFIED,
    detect_german_requirement,
)


def test_verhandlungssicher_deutsch_is_hard_requirement():
    result = detect_german_requirement("Wir erwarten verhandlungssicheres Deutsch in Wort und Schrift.")
    assert result["tier"] == TIER_REQUIRED_C1


def test_c1_near_deutsch_is_hard_requirement():
    result = detect_german_requirement("Deutschkenntnisse auf mindestens C1-Niveau erforderlich.")
    assert result["tier"] == TIER_REQUIRED_C1


def test_muttersprachlich_is_hard_requirement():
    result = detect_german_requirement("Sie sprechen Deutsch auf muttersprachlichem Niveau.")
    assert result["tier"] == TIER_REQUIRED_C1


def test_verhandlungssicheres_englisch_does_not_trigger_german_gate():
    result = detect_german_requirement("Verhandlungssicheres Englisch ist ein Muss.")
    assert result["tier"] == TIER_UNSPECIFIED


def test_sehr_gute_deutschkenntnisse_is_boilerplate_only():
    result = detect_german_requirement("Sehr gute Deutschkenntnisse und gute Englischkenntnisse.")
    assert result["tier"] == TIER_BOILERPLATE


def test_fliessend_deutsch_is_boilerplate():
    result = detect_german_requirement("Sie sprechen fließend Deutsch.")
    assert result["tier"] == TIER_BOILERPLATE


def test_english_working_language_detected():
    result = detect_german_requirement("Our working language is English. German is a plus.")
    assert result["tier"] == TIER_ENGLISH_FRIENDLY


def test_deutsch_von_vorteil_is_english_friendly():
    result = detect_german_requirement("Deutschkenntnisse sind von Vorteil, aber kein Muss.")
    assert result["tier"] == TIER_ENGLISH_FRIENDLY


def test_no_language_mention():
    result = detect_german_requirement("Sie analysieren Daten mit SQL und Python.")
    assert result["tier"] == TIER_UNSPECIFIED


def test_empty_text():
    assert detect_german_requirement(None)["tier"] == TIER_UNSPECIFIED
    assert detect_german_requirement("")["tier"] == TIER_UNSPECIFIED
