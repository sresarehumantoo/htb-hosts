#compdef htb-hosts
# zsh completion for htb-hosts. Install to a directory on $fpath as
# `_htb-hosts` (make install puts it in $PREFIX/share/zsh/site-functions).
#
# Candidate words come from `htb-hosts complete <kind>`, which prints one word
# per line and stays silent if /etc/hosts is unreadable -- so a broken hosts
# file degrades completion instead of spraying errors into the prompt.

_htb-hosts_words() {
  # $1 = completion kind, $2 = tag/description, $3... = patterns already typed
  local kind=$1 descr=$2
  shift 2
  # _call_program evals its arguments, so requote the typed words as literals;
  # otherwise a $(...) still sitting on the command line would run on <TAB>.
  local -a items scope
  scope=("${(@Q)@}")
  items=(${(f)"$(_call_program htb-hosts htb-hosts complete $kind ${(q)scope} 2>/dev/null)"})
  (( ${#items} )) && _describe -t "$kind" "$descr" items
}

# The pattern words typed so far, with options and their values removed. They
# scope the candidates, because a second pattern narrows the selection.
_htb-hosts_scope() {
  local word skip=0
  local -a out
  for word in ${words[2,CURRENT-1]}; do
    if (( skip )); then
      skip=0
    elif [[ $word == (-i|--index|--sep|--tag) ]]; then
      skip=1
    elif [[ $word != -* ]]; then
      out+=$word
    fi
  done
  print -r -- ${out}
}

# `get --host` prints hostnames, so the pattern should complete to the things
# that can actually come out of the command as typed.
_htb-hosts_patterns() {
  local kind=targets
  (( ${+opt_args[--host]} )) && kind=hosts
  local -a scope
  scope=(${(z)"$(_htb-hosts_scope)"})
  _htb-hosts_words $kind 'hostname, IP or tag' $scope
}

_htb-hosts() {
  local curcontext="$curcontext" state line ret=1
  typeset -A opt_args

  _arguments -C \
    '(- *)'{-h,--help}'[show help and exit]' \
    '1: :->command' \
    '*:: :->args' && ret=0

  case $state in
    command)
      local -a commands
      commands=(
        'add:add or update a host entry'
        'rm:remove a host by hostname or IP'
        'clean:remove managed entries (all, or by --tag/--match/--exclude)'
        'show:human-readable view of the managed entries'
        'get:print bare ip/hostname values for matching entries'
        'migrate:import existing /etc/hosts entries into the block'
        'retag:recompute all tags from box name'
        'backup:save an explicit snapshot of /etc/hosts'
        'restore:restore /etc/hosts from a snapshot'
        'doctor:check access + block integrity'
      )
      _describe -t commands 'htb-hosts command' commands && ret=0
      ;;

    args)
      case $words[1] in
        add)
          _arguments \
            '--tag[group tag (defaults to the box name)]:tag:->tags' \
            '--json[machine-readable output]' \
            '*:IP or hostname:->selectors' && ret=0
          ;;
        rm)
          _arguments \
            '--json[machine-readable output]' \
            '1:hostname or IP to remove:->selectors' && ret=0
          ;;
        clean)
          _arguments \
            '(--exclude --match)--tag[only remove entries with this exact tag]:tag:->tags' \
            '(--tag --exclude)*--match[remove entries matching every pattern]:pattern:->targets' \
            '(--tag --match)*--exclude[remove everything EXCEPT fuzzy matches]:pattern:->excludes' \
            {-y,--yes}'[skip the --match confirmation prompt]' \
            '--json[machine-readable output]' && ret=0
          ;;
        show)
          _arguments \
            '--flat[one line per entry, ungrouped]' \
            '--full[also list unmanaged/system entries]' \
            '--json[machine-readable output]' \
            '*:pattern:->targets' && ret=0
          ;;
        get)
          _arguments \
            '--host[print hostnames instead of IPs]' \
            {-i,--index}'[keep only these 1-based lines: 2, -1, 2-4, 1,3]:spec:' \
            '(--sep)'{-n,--numbered}'[number the values so you can pick one with --index]' \
            '(-n --numbered)--sep[join values with this instead of a newline]:separator:' \
            '--json[machine-readable output]' \
            '*:pattern:->targets' && ret=0
          ;;
        backup)
          _arguments \
            '--label[label appended to the backup filename]:label:' \
            '--json[machine-readable output]' && ret=0
          ;;
        restore)
          _arguments \
            '--list[list available backups]' \
            '--json[machine-readable output]' \
            '1:snapshot:->backups' && ret=0
          ;;
        migrate|retag|doctor)
          _arguments '--json[machine-readable output]' && ret=0
          ;;
      esac

      case $state in
        tags)      _htb-hosts_words tags 'box tag' && ret=0 ;;
        backups)   _htb-hosts_words backups 'snapshot' && ret=0 ;;
        selectors) _htb-hosts_words selectors 'managed hostname or IP' && ret=0 ;;
        excludes)  _htb-hosts_words targets 'hostname, IP or tag' && ret=0 ;;
        targets)   _htb-hosts_patterns && ret=0 ;;
      esac
      ;;
  esac

  return ret
}

_htb-hosts "$@"
