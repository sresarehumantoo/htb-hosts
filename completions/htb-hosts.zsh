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
  local -a items
  items=(${(f)"$(_call_program htb-hosts htb-hosts complete $kind "$@" 2>/dev/null)"})
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
    elif [[ $word == (-i|--index|--sep) ]]; then
      skip=1
    elif [[ $word != -* ]]; then
      out+=$word
    fi
  done
  print -r -- ${out}
}

# `--host` prints hostnames and `--both` hostnames + IPs, so the pattern should
# complete to the things that can actually come out.
_htb-hosts_pattern_kind() {
  if (( ${+opt_args[--host]} )); then
    print -r -- hosts
  elif (( ${+opt_args[--both]} )); then
    print -r -- selectors
  else
    print -r -- targets
  fi
}

_htb-hosts_patterns() {
  local kind=$(_htb-hosts_pattern_kind)
  local -a scope
  scope=(${(z)"$(_htb-hosts_scope)"})
  _htb-hosts_words $kind 'hostname, IP or tag' $scope
}

# The which-value / how-to-print flags shared by `show` and `get`. No hyphen in
# the name: zsh allows them in function names but not in parameter names, and an
# invalid assignment is silently reparsed as a command (with a confusing
# "unknown file attribute" glob error).
_htb_hosts_select_flags=(
  '(--host --both)--ip[print IPs only (default)]'
  '(--ip --both)--host[print hostnames only]'
  '(--ip --host)--both[print the IP and its hostnames]'
  '(--first)'{-i,--index}'[keep only these 1-based lines: 2, -1, 2-4, 1,3]:spec:'
  '(-i --index)--first[shorthand for --index 1]'
  '(--sep)'{-n,--numbered}'[number the values so you can pick one with --index]'
  '(-n --numbered)--sep[join values with this instead of a newline]:separator:'
  '--json[machine-readable output]'
)

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
        'clean:remove managed entries (all, or by --tag/--exclude)'
        'list:flat list of managed entries'
        'show:box-grouped overview, or bare values for a pattern'
        'get:print bare ip/hostname values for matching entries'
        'select:alias of get'
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
            '(--exclude)--tag[only remove entries with this exact tag]:tag:->tags' \
            '(--tag)*--exclude[remove everything EXCEPT fuzzy matches]:pattern:->excludes' \
            '--json[machine-readable output]' && ret=0
          ;;
        list)
          _arguments \
            '--tag[only list entries with this tag]:tag:->tags' \
            '--json[machine-readable output]' && ret=0
          ;;
        show)
          _arguments \
            '--full[also list unmanaged/system entries]' \
            $_htb_hosts_select_flags \
            '*:pattern or full:->showargs' && ret=0
          ;;
        get|select)
          _arguments \
            $_htb_hosts_select_flags \
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
        showargs)
          # `show` takes either the literal scope words or a fuzzy pattern.
          local -a scopes
          scopes=(
            'full:also list unmanaged/system entries'
            'all:also list unmanaged/system entries'
          )
          (( CURRENT == 2 )) && { _describe -t scopes 'scope' scopes && ret=0 }
          _htb-hosts_patterns && ret=0
          ;;
      esac
      ;;
  esac

  return ret
}

_htb-hosts "$@"
