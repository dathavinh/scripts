# scripts

## Requirements:
- python2.7
- pip10.0.1


## Installation:
`pip install -r < requirements.txt`


### Lunch Ordering from Now
- Create your `config.py` from `config.py.example`
- Adjust your config in `config.py`.
- Set crontab on your "server" 

`*  *  *  *  1-5  /usr/local/bin/python2.7 /Users/dat.havinh/scripts/lunch_ordering.py --env=prd`

That's it.
