/*
Copyright 2025 New Vector Ltd.
Copyright 2014 OpenMarket Ltd

SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
Please see LICENSE files in the repository root for full details.

Originally licensed under the Apache License, Version 2.0:
<http://www.apache.org/licenses/LICENSE-2.0>.
*/
-- Note that this SQL file is not up to date, and migrations can be found in sydent/db/sqlitedb.py

CREATE TABLE IF NOT EXISTS peers (
	id integer primary key,
	name varchar(255) not null,
	port integer default null,
	lastSentAssocsId integer default 0,
	lastSentInviteTokensId integer default 0,
	lastSentEphemeralKeysId integer default 0,
	lastPokeSucceededAt integer,
	active integer not null default 0,
	shadow integer not null default 0
);

CREATE UNIQUE INDEX IF NOT EXISTS name on peers(name);

CREATE TABLE IF NOT EXISTS peer_pubkeys (
	id integer primary key,
	peername varchar(255) not null,
	alg varchar(16) not null,
	key text not null,
	foreign key (peername) references peers (name)
);
CREATE UNIQUE INDEX IF NOT EXISTS peername_alg on peer_pubkeys(peername, alg);
