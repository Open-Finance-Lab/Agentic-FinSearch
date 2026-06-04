# Contributing to Agentic FinSearch

Thank you for your interest in contributing to Agentic FinSearch! This document provides guidelines and standards for contributing to the future of personalized search.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Definition of Done](#definition-of-done)
- [Code Standards](#code-standards)
- [Pull Request Process](#pull-request-process)
- [Code Review Guidelines](#code-review-guidelines)
- [Testing Guidelines](#testing-guidelines)
- [Documentation](#documentation)
- [Getting Help](#getting-help)

---

## Code of Conduct

We are committed to providing a welcoming and inclusive environment. Please:

- Be respectful and constructive in all interactions
- Welcome newcomers and help them get started
- Focus on what is best for the community and project
- Regularly communicate with our partner projects

---

## Getting Started

### Prerequisites

Before contributing, ensure you have Git / GitHub Desktop configured, API keys present and the project ready to go.
Refer to the [Agentic FinSearch documentation](https://agentic-finsearch.readthedocs.io/en/latest/) for setup instructions
---

## Development Workflow

### Branching Strategy

- `main` - Production-ready code
- `fingpt_backend_prod` - Backend production staging branch (PRs into main for releases)
- `fingpt_backend_dev` - Backend development branch (PRs into fingpt_backend_prod)
- `fingpt_mcp_demo` - Project lead's branch. Receives the most updates in the highest frequency.
- `doc` - Documentation updates branch
- `[yourUserName]_[yourFocus]` - Individual branch

### Making Changes

1. **Create your individual branch**

2. **Make Your Changes**
   - Follow the [Code Standards](#code-standards)
   - Write clean, maintainable code
   - Add comments for complex logic

3. **Test Your Changes**
   - Backend: `uv run python -m pytest tests/ -v` (from `Main/backend/`)
   - Frontend: `bun run build:full` (from `Main/frontend/`)
   - Always make sure the search agent still runs and answer questions normally before testing features you've changed / added.

4. **Commit Your Changes**

   Use clear, descriptive commit messages:
   - `feat: Add R2C context compression stats endpoint`
   - `fix: Resolve session isolation bug in context manager`
   - `docs: Update API documentation for MCP endpoints`
   - `refactor: Simplify context pipeline initialization`

---

## Definition of Done

A contribution is considered complete when ALL of the following criteria are met:

### Functionality
- [ ] Feature/fix works as intended
- [ ] No new bugs introduced
- [ ] Edge cases discussed and handled appropriately
- [ ] Error handling implemented where needed

### Code Quality
- [ ] Code follows project standards (see [Code Standards](#code-standards))
- [ ] No unnecessary complexity (KISS, YAGNI principles)
- [ ] Code is DRY (Don't Repeat Yourself)
- [ ] No commented-out code or debug statements unless necessary or otherwise asked to do so
- [ ] Lint-checked
- [ ] Code manually reviewed by another person (not AI-reviewed. If that person chooses to use AI that is their choice)

### Testing
- [ ] Manual testing completed successfully
- [ ] Existing functionality not broken
- [ ] Backend tested with `uv run python -m pytest tests/ -v` (if applicable)
- [ ] Frontend built successfully with `bun run build:full` (if applicable)

### Documentation
- [ ] Code comments added for complex logic
- [ ] Docstrings added for new functions/classes (Python)
- [ ] README.md updated if user-facing changes
- [ ] Main/README.md updated if project structure changes
- [ ] API documentation updated if endpoints changed

### Integration
- [ ] Code integrates cleanly with existing codebase
- [ ] No merge conflicts
- [ ] Dependencies documented in appropriate requirements files
- [ ] Environment variables documented in `Main/backend/.env.example`

### Review Readiness
- [ ] Self-reviewed before submitting PR
- [ ] PR description clearly explains changes
- [ ] Related issue linked (if applicable)
- [ ] Screenshots/videos included for UI changes
- [ ] Code manually reviewed by another person (not AI-reviewed. If that person chooses to use AI that is their choice)

---

## Code Standards

### General Principles

Follow these core principles when writing code:

1. **KISS** (Keep It Simple, Stupid) - Favor simple, straightforward solutions
2. **YAGNI** (You Aren't Gonna Need It) - Don't add functionality until needed. This is especially important if you vibe-code, as AI tends to like to add random stuff.
3. **DRY** (Don't Repeat Yourself) - Avoid code duplication

### Python Code Standards

- **PEP 8**: Follow Python style guidelines
- **Type Hints**: Use type hints for function parameters and returns
- **Docstrings**: Document all public functions, classes, and modules
- **Imports**: Group imports (stdlib, third-party, local)
- **Line Length**: Common-sense length please


### JavaScript Code Standards

- **ES6+**: Use modern JavaScript features
- **Consistency**: Match existing code style
- **Comments**: Explain complex logic and business rules
- **Modularity**: Keep functions focused and single-purpose. Don't be afraid of creating a lot of functions if it improves code-readability by e.g. breaking down a complex feature.

### Django-Specific Standards

- **Views**: Keep views thin, move processing / LLM communication logic to separate functions / files
- **Models**: Use descriptive field names and help_text
- **URLs**: Use clear, RESTful URL patterns. NO LOGIC PERMITTED.
- **Settings**: Never commit secrets or API keys

### File Organization

- **Backend Files**:
  - Views logic in `api/views.py`
  - Data processing in `datascraper/`
  - Configuration in `django_config/settings.py`

- **Frontend Files**:
  - Components in `src/modules/components/`
  - Styles in `src/modules/styles/`
  - Utilities in `src/modules/`

---

## Pull Request Process

### Before Submitting

1. **Verify Definition of Done**: Ensure all DoD criteria are met
2. **Update Documentation**: Update relevant docs (README, CLAUDE.md, etc.)
   - You don't have to update documentation inside Docs, though it will be much appreciated.
3. **Clean Commit History**: Squash minor/fixup commits if needed
4. **Rebase on Latest**: Always fetch & pull before submitting PR.

### Submitting a Pull Request

1. **Push Your Branch**

2. **Create Pull Request on GitHub**
   - Use a clear, descriptive title
   - Have basic PR description. Blank description is NOT allowed.
   - Reference related issues with `Working on #123` or `Fixes #456`

3. **PR Description Should Include**:
   - **What**: What changes were made
   - **How**: How the changes were implemented
   - **Screenshots**: For UI changes, include before/after screenshots

---

## Code Review Guidelines

### Important: Manual Review Policy

**Code reviews are NOT enforced automatically via GitHub PR checks**, but every pull request **MUST be reviewed manually by at least one person** before merging.

### Review Responsibilities

**For Authors:**
- Respond to feedback promptly and professionally
- Be open to suggestions and willing to make changes
- Explain your reasoning when disagreeing with feedback. Please no arguing.

**For Reviewers:**
- Review within one week, (or before the end of next RCOS class) when possible
- Be constructive and respectful in feedback
- Test the changes locally when appropriate
- Don't forget to use common sense

### Review Checklist

Reviewers should verify:

- [ ] **Functionality**: Does it work as intended?
- [ ] **Code Quality**: Is it clean, readable, and maintainable?
- [ ] **Standards**: Does it follow project coding standards?
- [ ] **Security**: Are there any security concerns?
- [ ] **Performance**: Are there performance implications?
- [ ] **Documentation**: Is documentation adequate?
- [ ] **Tests**: Has it been adequately tested?
- [ ] **Integration**: Does it integrate well with existing code?

### Approval Process

1. At least **one approval** required before merging in most cases
2. All review comments must be addressed (resolved or discussed)
3. Obviously, no merge conflicts

---

## Testing Guidelines

### Manual Testing

Manual testing is the primary testing method for this project:

   - Start the backend, rebuild the frontend and load extension in Chrome
   - Test all affected workflows end-to-end
   - Test with different API providers (OpenAI, DeepSeek, Anthropic)

### Test Scenarios to Cover

- **Happy Path**: Normal user flows work correctly
- **Jerk Path**: Users and customers are the dumbest creatures and laziest shit in the entire universe. Think from their perspective what ridiculous things they might do.
- **Error Cases**: Most common errors are handled gracefully
- **Edge Cases**: Boundary conditions work as expected
- **Session Management**: Context isolation between sessions
- **API Integration**: All supported models work correctly

---

## Documentation

### When to Update Documentation

Update the REAMEs or other .md files when you:

- Add new features or endpoints
- Drastically changes existing functionality
- Modify architecture or data flow
- Add new dependencies or requirements
- Change installation or setup process

### Documentation Files

- **README.md**: User-facing installation and usage
- **CLAUDE.md**: Architecture, development commands, key components
- **CONTRIBUTING.md**: This file - contribution guidelines
- **Docs/**: Sphinx documentation (much appreciated if you can take some time to update this!)

### Docstring Guidelines (Python)

Use Google-style docstrings:

```python
def function_name(param1: str, param2: int) -> bool:
    """
    Brief description of function.

    More detailed explanation if needed.

    Args:
        param1: Description of param1
        param2: Description of param2

    Returns:
        Description of return value

    Raises:
        ValueError: When invalid input provided
    """
    pass
```

---

## Getting Help

Always double-check existing documentation and issues first before asking for help!

### Resources

- **Documentation**: https://agentic-finsearch.readthedocs.io/
- **Issues**: Check existing issues on GitHub
- **Communication**: Message or email the project lead and / or start a discussion in Discord

### Reporting Bugs

- Create a new issue if spotted any bug
- Don't forget common sense: Include descriptive title, description and / or screenshots.

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

## Academic Attribution

If your contribution is used in academic work, it may be cited according to:

```bibtex
@article{Tian2024CustomizedFinGPT,
	doi = {10.48550/ARXIV.2410.15284},
	url = {https://arxiv.org/abs/2410.15284},
	author = {Felix Tian, Ajay Byadgi, Daniel Kim, Daochen Zha, Matt White, Kairong Xiao, Xiao-Yang Liu},
	keywords = {Computational Engineering, Finance, and Science (cs.CE), Human-Computer Interaction (cs.HC), FOS: Computer and information sciences, FOS: Computer and information sciences},
	title = {Customized FinGPT Search Agents Using Foundation Models},
	publisher = {arXiv},
	year = {2024}
}
```

---

**Thank you for contributing to Agentic FinSearch!**

**Disclaimer**: We are sharing code for academic purposes under the MIT education license. Nothing herein is financial advice, and NOT a recommendation to trade real money. Please use common sense and always first consult a professional before trading or investing.
