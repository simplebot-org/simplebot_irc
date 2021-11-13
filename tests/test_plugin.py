class TestPlugin:
    """Online tests"""

    def test_join(self, mocker) -> None:
        msgs = mocker.get_replies("/join #simplebot-irc-ci")
        assert msgs

    def test_nick(self, mocker) -> None:
        msg = mocker.get_one_reply("/nick")
        assert msg.text

        msg = mocker.get_one_reply("/nick simplebotci")
        assert msg.text

    def test_names(self, mocker) -> None:
        msg = mocker.get_one_reply("/names")
        assert "❌" in msg.text
