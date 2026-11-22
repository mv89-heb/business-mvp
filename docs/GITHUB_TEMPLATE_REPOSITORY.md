# GitHub Template Repository

The source repository used to export this starter also contains a live customer configuration. **Do not enable that source repository as a GitHub template.**

1. Export a clean starter to a new folder using the command in `docs/CLONING.md`.
2. Create a new private GitHub repository for the exported folder and push its contents.
3. Verify that the new repository contains no customer identity, contact details, leads, database settings, secrets, uploads or logs.
4. Run CI and an independent deployment with a new database and credentials.
5. Only then open Settings → General and enable **Template repository** on the clean repository.
6. Create each later customer repository with **Use this template** and follow `docs/CLONING.md`.

Repository template settings and creation of a new GitHub repository are account-level GitHub actions; the application exporter does not change them automatically.
