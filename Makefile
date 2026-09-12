# htb-hosts - sudo-free, cleanable /etc/hosts management for HTB
#
# `make install` works either way:
#   make install       # as your normal user; privileged steps call sudo themselves
#   sudo make install  # SUDO_USER is honored, so the ACL + MCP still target you
# The human user is auto-detected (SUDO_USER when under sudo, else the current
# user); override explicitly with HOSTS_USER=<name>.

SHELL      := /bin/bash
PREFIX     ?= /usr/local
BINDIR     ?= $(PREFIX)/bin
LIBDIR     ?= /opt/htb-hosts
# The real human user even when invoked via `sudo make ...`.
REAL_USER  := $(shell echo "$${SUDO_USER:-$$(id -un)}")
HOSTS_USER ?= $(REAL_USER)
HOSTS_FILE ?= /etc/hosts
SUDO       ?= sudo

# Both are on the default search paths: /usr/local/share/zsh/site-functions is
# in zsh's stock $fpath, and bash-completion looks under every XDG_DATA_DIRS
# entry (default /usr/local/share:/usr/share).
ZSH_COMPDIR  ?= $(PREFIX)/share/zsh/site-functions
BASH_COMPDIR ?= $(PREFIX)/share/bash-completion/completions

CLI     := $(BINDIR)/htb-hosts
MCP_CMD := python3 $(LIBDIR)/mcp_server.py

.DEFAULT_GOAL := help

.PHONY: help install install-files install-acl mcp completions remove purge \
        migrate lint lint-completions format test smoke reinstall clean

help: ## Show this help
	@echo "htb-hosts targets:"
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Vars: HOSTS_USER=$(HOSTS_USER) LIBDIR=$(LIBDIR) BINDIR=$(BINDIR) SUDO=$(SUDO)"

install: install-files install-acl completions mcp ## Install everything (files + ACL + completions + MCP)
	@if [ "$(HOSTS_USER)" = root ]; then \
		echo "!! HOSTS_USER=root — no unprivileged user got the ACL, so no-sudo use"; \
		echo "   won't work. Run 'make install' as your normal user, or pass"; \
		echo "   HOSTS_USER=<name>."; \
	fi
	@echo "==> installed. Try: htb-hosts doctor"

install-files: ## Copy the library and CLI into place (needs sudo)
	$(SUDO) install -d -m 0755 $(LIBDIR)
	$(SUDO) install -m 0644 src/htb_hosts.py  $(LIBDIR)/htb_hosts.py
	$(SUDO) install -m 0755 src/mcp_server.py $(LIBDIR)/mcp_server.py
	$(SUDO) install -m 0755 src/htb-hosts     $(CLI)
	$(SUDO) chmod -R go-w $(LIBDIR)  # incl. any __pycache__ from earlier runs

completions: ## Install the zsh + bash tab completions
	$(SUDO) install -d -m 0755 $(ZSH_COMPDIR) $(BASH_COMPDIR)
	$(SUDO) install -m 0644 completions/htb-hosts.zsh  $(ZSH_COMPDIR)/_htb-hosts
	$(SUDO) install -m 0644 completions/htb-hosts.bash $(BASH_COMPDIR)/htb-hosts
	@echo "==> completions installed (start a new shell, or run 'rm -f ~/.zcompdump && compinit')"

install-acl: ## Grant $(HOSTS_USER) sudo-free write on $(HOSTS_FILE) via ACL
	$(SUDO) setfacl -m u:$(HOSTS_USER):rw $(HOSTS_FILE)
	@getfacl -p $(HOSTS_FILE) 2>/dev/null | grep -q "user:$(HOSTS_USER):rw" \
		&& echo "==> ACL granted to $(HOSTS_USER)" || echo "!! ACL not confirmed"

mcp: ## Register the MCP server for the human user (honors SUDO_USER; idempotent)
	@u="$(HOSTS_USER)"; \
	if [ "$$u" != "$$(id -un)" ]; then RUN="$(SUDO) -u $$u -H"; else RUN=""; fi; \
	if $$RUN sh -c 'command -v claude >/dev/null 2>&1'; then \
		$$RUN claude mcp remove -s user htb-hosts >/dev/null 2>&1 || true; \
		$$RUN claude mcp add -s user htb-hosts -- $(MCP_CMD); \
		echo "==> MCP registered for $$u"; \
	else \
		echo "-- claude CLI not found for $$u; skipping MCP registration."; \
		echo "   The htb-hosts CLI still works. Register later with:"; \
		echo "       claude mcp add -s user htb-hosts -- $(MCP_CMD)"; \
	fi

migrate: ## One-time import of existing /etc/hosts entries into the managed block
	$(CLI) migrate

remove: ## Uninstall software (leaves /etc/hosts entries + ACL untouched)
	-claude mcp remove -s user htb-hosts 2>/dev/null
	$(SUDO) rm -f $(CLI) $(ZSH_COMPDIR)/_htb-hosts $(BASH_COMPDIR)/htb-hosts
	$(SUDO) rm -rf $(LIBDIR)
	@echo "==> removed binaries + completions + MCP registration."
	@echo "   /etc/hosts entries and the ACL are left intact."
	@echo "   Run 'make purge' instead to also wipe them."

purge: ## Full removal: managed block + ACL + software
	-$(CLI) clean 2>/dev/null
	-$(SUDO) setfacl -x u:$(HOSTS_USER) $(HOSTS_FILE) 2>/dev/null
	-claude mcp remove -s user htb-hosts 2>/dev/null
	$(SUDO) rm -f $(CLI) $(ZSH_COMPDIR)/_htb-hosts $(BASH_COMPDIR)/htb-hosts
	$(SUDO) rm -rf $(LIBDIR)
	@echo "==> purged: managed block cleared, ACL removed, software uninstalled."

reinstall: remove install ## Remove then install fresh

# Conventions that are expected/noisy in a unittest suite but not in library code.
TEST_LINT_SKIP := missing-docstring,too-few-public-methods,duplicate-code,protected-access

lint: ## Run black --check and pylint on src (strict 10.0) and tests (relaxed)
	black --check src/htb_hosts.py src/mcp_server.py src/htb-hosts tests/*.py
	@tmpdir=$$(mktemp -d) && cp src/htb-hosts $$tmpdir/_htb_hosts_cli.py && \
		pylint src/htb_hosts.py src/mcp_server.py $$tmpdir/_htb_hosts_cli.py; \
		rc=$$?; rm -rf $$tmpdir; [ $$rc -eq 0 ] || exit $$rc
	pylint --disable=$(TEST_LINT_SKIP) tests/*.py

lint-completions: ## Syntax-check the shell completion scripts
	bash -n completions/htb-hosts.bash
	@if command -v zsh >/dev/null 2>&1; then \
		zsh -n completions/htb-hosts.zsh && echo "==> zsh + bash completions parse"; \
	else \
		echo "-- zsh not installed; checked bash only"; \
	fi

format: ## Auto-format the sources and tests with black
	black src/htb_hosts.py src/mcp_server.py src/htb-hosts tests/*.py

clean: ## Remove Python bytecode caches
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; rm -rf .pylint.d

test: ## Run the unittest suite (stdlib only, no root, no /etc/hosts touched)
	cd tests && python3 -m unittest discover -p 'test_*.py' -v

smoke: ## Quick end-to-end CLI check against a throwaway hosts file
	@set -e; f=$$(mktemp); d=$$(mktemp -d); \
		export HTB_HOSTS_FILE=$$f HTB_HOSTS_BACKUP_DIR=$$d; \
		PY=src/htb-hosts; \
		python3 $$PY add machine.htb 10.129.45.2 www.machine.htb; \
		python3 $$PY add 10.129.223.2 "sequel.htb, dc01.sequel.htb"; \
		[ "$$(python3 $$PY get sequel)" = 10.129.223.2 ] \
			|| (echo "get did not return the bare IP" && exit 1); \
		[ "$$(python3 $$PY get dc01 --host)" = dc01.sequel.htb ] \
			|| (echo "get <pattern> did not select the hostname" && exit 1); \
		[ "$$(python3 $$PY get sequel --host -i 1)" = sequel.htb ] \
			|| (echo "--index 1 did not pick the first of two hostnames" && exit 1); \
		[ "$$(python3 $$PY get sequel --host -i -1)" = dc01.sequel.htb ] \
			|| (echo "--index -1 did not pick the last hostname" && exit 1); \
		[ "$$(python3 $$PY get sequel --host --sep ,)" = sequel.htb,dc01.sequel.htb ] \
			|| (echo "--sep did not join onto one line" && exit 1); \
		python3 $$PY get sequel --host -i 9 >/dev/null 2>&1 \
			&& (echo "an out-of-range index should fail" && exit 1) || true; \
		python3 $$PY get nosuchbox >/dev/null 2>&1 \
			&& (echo "get should fail when nothing matches" && exit 1) || true; \
		python3 $$PY complete tags | grep -qx sequel \
			|| (echo "complete tags missing the sequel box" && exit 1); \
		[ "$$(python3 $$PY complete hosts sequel dc01)" = dc01.sequel.htb ] \
			|| (echo "complete did not scope to the typed patterns" && exit 1); \
		[ "$$(python3 $$PY get sequel dc01 --host)" = dc01.sequel.htb ] \
			|| (echo "a second pattern did not narrow the selection" && exit 1); \
		python3 $$PY complete commands | grep -qx get \
			|| (echo "complete commands missing get" && exit 1); \
		python3 $$PY backup --label smoke; \
		python3 $$PY show --flat; \
		[ "$$(python3 $$PY show machine --flat | wc -l)" = 2 ] \
			|| (echo "show PATTERN did not filter" && exit 1); \
		python3 $$PY clean --match machine --yes; \
		python3 $$PY clean --exclude sequel; \
		python3 $$PY clean; \
		python3 $$PY restore; \
		python3 $$PY show --flat; \
		python3 $$PY clean; \
		! grep -q '>>> htb-hosts' $$f || (echo "managed block not removed" && exit 1); \
		rm -rf $$f $$d; echo "==> smoke test passed"
