# singleton state class
class AbbrState:
    state = {}

    @staticmethod
    def get(src, abbr, title=None):
        state = AbbrState.state

        if abbr[-1] == 's':
            plural = True
            titleKey = 'plural'
            abbrKey = abbr[:-1]
        else:
            plural = False
            titleKey = 'singular'
            abbrKey = abbr

        key = (src, abbrKey)

        if key in state:
            obj = state[key]
            if title is not None and titleKey in obj and title != obj[titleKey]:
                detail = '"%s": "%s" != "%s"' % (abbr, obj[titleKey], title)
                raise ValueError('Inconsistent abbreviation ' + detail)
            elif titleKey not in obj:
                if title is not None:
                    obj[titleKey] = title
                else:
                    return None

            obj['count'] += 1
        elif title is None:
            return None
        else:
            state[key] = obj = {'count': 0}
            obj[titleKey] = title

        return {
            'count': obj['count'],
            'abbr': abbr,
            'title': obj[titleKey]
        }
