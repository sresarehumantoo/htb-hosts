#compdef htb-hosts
# zsh completion for htb-hosts. Install to a directory on $fpath as
# `_htb-hosts` (make install puts it in $PREFIX/share/zsh/site-functions).
#
# Candidate words come from `htb-hosts complete <kind>`, which prints one word
# per line and stays silent if /etc/hosts is unreadable -- so a broken hosts
# file degrades completion instead of spraying errors into the prompt.

_htb-hosts_words() {
  # $1 = completion kind, $2 = tag/description for the menu
  local -a items
  items=(${(f)"$(_call_program htb-hosts htb-hosts complete $1 2>/dev/null)"})
  (( ${#items} )) && _describe -t "$1" "$2" items
}

_htb-hosts_field_flags() {
  _values 'field' \
    '--ip[print IPs only (default)]' \
    '--host[print hostnames only]' \
    '--both[print the IP and its hostnames]'
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
            '(--tag)*--exclude[remove everything EXCEPT fuzzy matches]:pattern:->targets' \
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
            '(--host --both)--ip[print IPs only (default)]' \
            '(--ip --both)--host[print hostnames only]' \
            '(--ip --host)--both[print the IP and its hostnames]' \
            '--json[machine-readable output]' \
            '*:pattern or full:->showargs' && ret=0
          ;;
        get|select)
          _arguments \
            '(--host --both)--ip[print IPs only (default)]' \
            '(--ip --both)--host[print hostnames only]' \
            '(--ip --host)--both[print the IP and its hostnames]' \
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
        targets)   _htb-hosts_words targets 'hostname, IP or tag' && ret=0 ;;
        showargs)
          # `show` takes either the literal scope words or a fuzzy pattern.
          local -a scopes
          scopes=(
            'full:also list unmanaged/system entries'
            'all:also list unmanaged/system entries'
          )
          (( CURRENT == 2 )) && { _describe -t scopes 'scope' scopes && ret=0 }
          _htb-hosts_words targets 'hostname, IP or tag' && ret=0
          ;;
      esac
      ;;
  esac

  return ret
}

_htb-hosts "$@"
