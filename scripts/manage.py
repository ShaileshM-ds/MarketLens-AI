
"""Run with PYTHONPATH=backend (and local dependency directory, when needed)."""

import argparse
import getpass
import os
import uuid


p = argparse.ArgumentParser()

p.add_argument(
    'command',
    choices=['seed', 'seed-history', 'admin']
)

p.add_argument(
    '--mode',
    default='demo',
    choices=['demo', 'imported', 'live']
)

p.add_argument('--username', default='admin')
p.add_argument('--reset', action='store_true')
p.add_argument('--days', type=int, default=180)
p.add_argument('--seed', dest='seed_value', type=int, default=42)

args = p.parse_args()

if os.getenv('REVIEW_MODE', '').strip().lower() in {
    '1', 'true', 'yes', 'on'
}:
    p.error('Management writes are disabled in review mode')


if args.command == 'seed-history':
    if args.mode != 'imported':
        p.error('seed-history must use --mode imported')

    if args.days < 1:
        p.error('--days must be at least 1')

    confirmation = input(
        'Generate simulated history in the imported database? '
        'Type YES to continue: '
    )

    if confirmation != 'YES':
        print('Cancelled. No database changes made.')
        raise SystemExit(0)

    # Import database-related modules only after confirmation.
    from app.db import database, indexes
    from app.services.history import seed_history

    db = database('imported')
    indexes(db)

    result = seed_history(
        db,
        days=args.days,
        seed=args.seed_value,
        reset=args.reset,
    )

    print(result)

else:
    # Existing commands retain their original behavior.
    from app.db import database, indexes

    db = database(args.mode)
    indexes(db)

    if args.command == 'seed':
        from app.services.demo import seed
        print(seed(db, args.reset))

    else:
        from app.services.auth import hash_password

        password = getpass.getpass(
            'New admin password (12+ characters): '
        )

        db.users.update_one(
            {'username': args.username},
            {
                '$set': {
                    'password': hash_password(password),
                    'role': 'admin',
                },
                '$setOnInsert': {
                    'id': uuid.uuid4().hex,
                    'username': args.username,
                },
            },
            upsert=True,
        )

        print('Administrator configured for ' + args.mode)
