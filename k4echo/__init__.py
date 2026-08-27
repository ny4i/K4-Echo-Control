"""K4-Echo-Control: voice control for an Elecraft K4 over its CAT-over-TCP port.

The package is shared by both halves of the system so that they cannot drift:

* ``k4echo.commands``   -- the allow-list of CAT commands the skill can send
* ``k4echo.signing``    -- HMAC request signing for the Lambda -> bridge hop
* ``k4echo.radio``      -- the TCP client that actually talks to the K4
* ``k4echo.alexa``      -- Alexa request parsing / response building (Lambda)
* ``k4echo.transports`` -- how the Lambda reaches the bridge (Lambda)
* ``k4echo.config``     -- bridge configuration loading (bridge)
* ``k4echo.bridge``     -- the daemon that runs on the home network
"""

__version__ = "1.0.0"
