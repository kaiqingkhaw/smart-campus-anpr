# Put this project on GitHub

Publish the extracted `smart-campus-anpr` folder, not the ZIP file. It contains the source and documentation reviewers should be able to browse.

The complete ZIP contains local model and evaluation files. Do not upload the ZIP or drag the complete folder into the GitHub website. Use Git commands below so `.gitignore` excludes those files. Never use `git add -f` for them.

1. Sign in to GitHub and create an empty repository named `smart-campus-anpr`. Leave the generated README, license and gitignore options unchecked because this folder already contains its own files.
2. Open a terminal in the extracted project folder. Run:

```powershell
git init -b main
git add .
git diff --cached --stat
git commit -m "Prepare Smart Campus ANPR portfolio source"
```

3. Copy the HTTPS repository URL from GitHub. Replace `YOUR_USERNAME` below and run:

```powershell
git remote add origin https://github.com/YOUR_USERNAME/smart-campus-anpr.git
git push -u origin main
```

If Git requests your identity, configure your own Git name and email before committing. Sign in using your Git credential manager when prompted; do not embed access tokens in these commands.

4. Check the Actions tab for the Python checks result. Add the repository description from `PORTFOLIO.md`, and pin the repository to your profile.
5. Add a demonstration recording and measured evaluation results when available.

Source: [GitHub's guide to adding locally hosted code](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github).

GitHub hosting of source does not run the Flask/MySQL/hardware system. This package has not been uploaded to your account.
