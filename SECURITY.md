# Security

Treat input documents as untrusted. The application uses bounded parsers, isolated workers, output validation and conservative failure states; these are not a guarantee against all malicious files or native decoder vulnerabilities. Keep Python and native dependencies updated.

Do not post real books, secrets, home-directory paths, databases or unredacted forensic packages in public issues. For a security concern, use GitHub private vulnerability reporting when enabled; otherwise contact the repository owner through an available private channel before sharing exploit material. There is no promised response SLA.

Reports should identify the version, platform, affected component and a minimal synthetic reproduction where possible. Source books must remain unchanged. Only the latest release is maintained; native Windows acceptance is tracked separately from Linux acceptance.
