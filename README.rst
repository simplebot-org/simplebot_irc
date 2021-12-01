IRC/DeltaChat Bridge
====================

.. image:: https://img.shields.io/pypi/v/simplebot_irc.svg
   :target: https://pypi.org/project/simplebot_irc

.. image:: https://img.shields.io/pypi/pyversions/simplebot_irc.svg
   :target: https://pypi.org/project/simplebot_irc

.. image:: https://pepy.tech/badge/simplebot_irc
   :target: https://pepy.tech/project/simplebot_irc

.. image:: https://img.shields.io/pypi/l/simplebot_irc.svg
   :target: https://pypi.org/project/simplebot_irc

.. image:: https://github.com/simplebot-org/simplebot_irc/actions/workflows/python-ci.yml/badge.svg
   :target: https://github.com/simplebot-org/simplebot_irc/actions/workflows/python-ci.yml

.. image:: https://img.shields.io/badge/code%20style-black-000000.svg
   :target: https://github.com/psf/black

An IRC/DeltaChat bridge plugin for `SimpleBot`_.

By default the bot will connect to ``irc.libera.chat:6667`` to change the IRC server::

    simplebot -a bot@example.com db -s simplebot_irc/host "irc.example.com:6667"

To change the bot's nick::

    simplebot -a bot@example.com db -s simplebot_irc/nick "DeltaBridge"

The bot will upload the files sent in the DeltaChat side to an uploads server (https://0x0.st/ by default), you can change the uploads server with::

    simplebot -a bot@example.com db -s simplebot_irc/uploads_url "https://example.com"

Install
-------

To install run::

  pip install simplebot-irc


.. _SimpleBot: https://github.com/simplebot-org/simplebot
