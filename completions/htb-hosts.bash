# bash completion for htb-hosts. Install as
# $XDG_DATA_DIRS/bash-completion/completions/htb-hosts (make install puts it in
# $PREFIX/share/bash-completion/completions, which bash-completion searches by
# default via XDG_DATA_DIRS=/usr/local/share:/usr/share).
#
# Candidate words come from `htb-hosts complete <kind>`, which prints one word
# per line and stays silent if /etc/hosts is unreadable.

# Fill COMPREPLY from `htb-hosts complete <kind>`, filtered by the current word.
_htb_hosts_words() {
    local words
    words=$(htb-hosts complete "$1" 2>/dev/null) || return 0
    mapfile -t COMPREPLY < <(compgen -W "$words $2" -- "$cur")
}

_htb_hosts() {
    local cur prev cmd i
    cur=${COMP_WORDS[COMP_CWORD]}
    prev=${COMP_WORDS[COMP_CWORD-1]}

    # The first non-option word after the program name is the subcommand.
    cmd=""
    for ((i = 1; i < COMP_CWORD; i++)); do
        case ${COMP_WORDS[i]} in
            -*) ;;
            *) cmd=${COMP_WORDS[i]}; break ;;
        esac
    done

    if [[ -z $cmd ]]; then
        _htb_hosts_words commands
        return
    fi

    # Option values first: they override the positional rules below.
    case $prev in
        --tag)
            _htb_hosts_words tags
            return
            ;;
        --exclude)
            _htb_hosts_words targets
            return
            ;;
        --label)
            return  # free-form text, nothing to suggest
            ;;
    esac

    local opts=""
    case $cmd in
        add)     opts="--tag --json" ;;
        rm)      opts="--json" ;;
        clean)   opts="--tag --exclude --json" ;;
        list)    opts="--tag --json" ;;
        show)    opts="--full --ip --host --both --json" ;;
        get|select) opts="--ip --host --both --json" ;;
        backup)  opts="--label --json" ;;
        restore) opts="--list --json" ;;
        *)       opts="--json" ;;
    esac

    if [[ $cur == -* ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$opts" -- "$cur")
        return
    fi

    case $cmd in
        get|select)     _htb_hosts_words targets ;;
        add|rm)         _htb_hosts_words selectors ;;
        restore)        _htb_hosts_words backups ;;
        show)           _htb_hosts_words targets "full all" ;;
    esac
}

complete -F _htb_hosts htb-hosts
