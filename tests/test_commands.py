from k4echo import commands


def test_power_off_is_the_command_the_skill_was_built_for():
    assert commands.POWER_OFF.cat == "PS0;"
    assert commands.POWER_OFF.expects_reply is False


def test_catalog_lookup_is_case_and_space_insensitive():
    assert commands.lookup("  Power_Off ") is commands.POWER_OFF
    assert commands.lookup("power_on") is commands.POWER_ON
    assert commands.lookup("nonsense") is None
    assert commands.lookup("") is None


def test_every_cat_string_is_semicolon_terminated():
    for command in commands.CATALOG.values():
        assert command.cat.endswith(";")


def test_power_reply_is_translated_to_speech():
    assert commands.describe_power_reply("PS1;") == "The K four is on."
    assert commands.describe_power_reply("PS0;") == "The K four is in standby."
    assert "did not answer" in commands.describe_power_reply("")
    assert "XX99" in commands.describe_power_reply("XX99;")
