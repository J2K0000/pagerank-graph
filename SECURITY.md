# Security

## Supported versions

Only the latest published version (`main`) receives security fixes.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private reporting: the repository's **Security** tab, then
**Report a vulnerability**. Describe the vulnerability, the affected version (`prgraph --version`) and, if
possible, the steps to reproduce it. The report stays private until a fix is published.

## In scope

- **The local server** (`prgraph serve`): it listens on 127.0.0.1 only, checks the origin and host of
  requests and requires a token on every action. Any request accepted from another site, any file read
  outside the analyses folder, or any command execution is a vulnerability.
- **The HTML report** (`report.html`): titles, anchors and URLs come from crawled sites and are treated as
  hostile. Any code executed, or any report made unusable, by crawled data is a vulnerability.
- **The exports** (`*.csv`, `report.xlsx`): a cell interpreted as a formula by a spreadsheet is a
  vulnerability.
- **The crawler**: failing to respect `robots.txt` or the delay between requests is treated as a serious
  defect.

Google credentials (Search Console, GA4) are never stored in the project: they are read from the
`GOOGLE_APPLICATION_CREDENTIALS` environment variable or from a file passed as an option.
