"""Weather watches, warnings and advisories for the configured location.

US locations come from the National Weather Service, Canadian ones from Environment
Canada; both are normalised to the same alert dict (see ``model``) and published as
``weather.alerts``. Two boards read it: ``weather.alerts`` cycles through what is in
force, ``weather.alert`` interrupts the rotation when a new one appears.
"""
