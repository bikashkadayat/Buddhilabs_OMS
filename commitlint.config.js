// Conventional Commits ruleset for the NIF Office Management System.
// Enforced in CI (see .github/workflows/ci.yml -> commitlint) and optionally
// locally via husky (see docs/CONTRIBUTING.md).
export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [2, 'always', [
      'feat', 'fix', 'perf', 'refactor', 'security', 'test',
      'docs', 'build', 'ci', 'chore', 'revert',
    ]],
    'scope-empty': [1, 'never'],           // encourage a scope, e.g. fix(leaves): ...
    'subject-case': [0],                   // allow any subject case
    'header-max-length': [2, 'always', 100],
  },
};
