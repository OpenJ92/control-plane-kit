Source: [extraction_parity/tests/__init__.py](../../../../extraction_parity/tests/__init__.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This docstring-only test package marker contains no setup, registrations or
fixtures. The tests beside it exercise the current extraction/parity tooling;
they are distinct from the historical tests discovered inside the frozen image.
Read each test owner for its fixture and evidence boundary. The package name
alone does not establish that a historical suite ran, a current successor passed
or a migration is complete.
