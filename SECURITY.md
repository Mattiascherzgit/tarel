# Security policy

## Supported versions

TAREL is pre-alpha. Security fixes are applied to the latest development branch and, after the
first release, to the most recent published version.

## Reporting a vulnerability

Please report vulnerabilities through GitHub's private vulnerability reporting for this
repository. Do not open a public issue for a suspected vulnerability, exposed credential, or data
leak.

Useful reports include the affected command or module, impact, minimal reproduction, and a
suggested mitigation when available. Remove real credentials, connection strings, sample values,
and proprietary schema details before submitting evidence.

Relevant security boundaries include:

- credential or connection-URL disclosure;
- connector write access or SQL injection;
- unbounded or unsafe source sampling;
- provider transmission without explicit user intent;
- annotation leakage of protected sample values;
- unsafe model downloads or checksum bypasses;
- path traversal or writes outside expected local stores;
- activation of unreviewed generated connector code.

Only test systems and data for which you have explicit authorization. Reports are handled on a
best-effort basis while the project remains pre-alpha.
