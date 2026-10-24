# bash completion for htb-hosts. Install as
# $XDG_DATA_DIRS/bash-completion/completions/htb-hosts (make install puts it in
# $PREFIX/share/bash-completion/completions, which bash-completion searches by
# default via XDG_DATA_DIRS=/usr/local/share:/usr/share).
#
# Candidate words come from `htb-hosts complete <kind>`, which prints one word
# per line and stays silent if /etc/hosts is unreadable.

# Fill COMPREPLY from `htb-hosts complete <kind> [scope...]`, filtered by $cur.
# $2 is extra literal words to offer alongside; $3.. scope the candidates.
# Filtered by hand rather than with `compgen -W`, which runs expansions (even
# command substitution) on its word list, and these words come from /etc/hosts.
_htb_hosts_words() {
    local kind=$1 extra=$2 word
    shift 2
    COMPREPLY=()
    while IFS= read -r word; do
        [[ -n $word && $word == "$cur"* ]] && COMPREPLY+=("$word")
    done < <(htb-hosts complete "$kind" "$@" 2>/dev/null; printf '%s\n' $extra)
}

# Complete a `get`/`show` pattern: scoped to the patterns already typed (a second
# pattern narrows) and restricted to what the chosen field can actually print.
_htb_hosts_patterns() {
    local word kind=targets skip=0
    local -a scope
    for word in "${COMP_WORDS[@]:2:COMP_CWORD-2}"; do
        if ((skip)); then
            skip=0
        elif [[ $word == -i || $word == --index || $word == --sep || $word == --tag ]]; then
            skip=1
        elif [[ $word == --host ]]; then
            kind=hosts
        elif [[ $word != -* ]]; then
            scope+=("$word")
        fi
    done
    _htb_hosts_words "$kind" "$1" "${scope[@]}"
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
        _htb_hosts_words commands ""
        return
    fi

    # Option values first: they override the positional rules below.
    case $prev in
        --tag)
            _htb_hosts_words tags ""
            return
            ;;
        --exclude)
            _htb_hosts_words targets ""   # --exclude is additive: never scope it
            return
            ;;
        --match)
            _htb_hosts_patterns ""
            return
            ;;
        --label|-i|--index|--sep)
            return  # free-form text, nothing to suggest
            ;;
    esac

    local opts=""
    case $cmd in
        add)     opts="--tag --json" ;;
        rm)      opts="--json" ;;
        clean)   opts="--tag --match --exclude -y --yes --json" ;;
        show)    opts="--flat --full --json" ;;
        get)     opts="--host -i --index -n --numbered --sep --json" ;;
        backup)  opts="--label --json" ;;
        restore) opts="--list --json" ;;
        *)       opts="--json" ;;
    esac

    if [[ $cur == -* ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$opts" -- "$cur")
        return
    fi

    case $cmd in
        get|show)       _htb_hosts_patterns "" ;;
        add|rm)         _htb_hosts_words selectors "" ;;
        restore)        _htb_hosts_words backups "" ;;
    esac
}

complete -F _htb_hosts htb-hosts
