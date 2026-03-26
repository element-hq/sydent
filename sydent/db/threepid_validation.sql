/*
Copyright 2025 New Vector Ltd.
Copyright 2014 OpenMarket Ltd

SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
Please see LICENSE files in the repository root for full details.

Originally licensed under the Apache License, Version 2.0:
<http://www.apache.org/licenses/LICENSE-2.0>.
*/

-- Note that this SQL file is not up to date, and migrations can be found in sydent/db/sqlitedb.py

CREATE TABLE IF NOT EXISTS threepid_validation_sessions (
	id integer primary key,
	medium varchar(16) not null,
	address varchar(256) not null,
	clientSecret varchar(32) not null,
	validated int default 0,
	mtime bigint not null
);

CREATE TABLE IF NOT EXISTS threepid_token_auths (
	id integer primary key,
	validationSession integer not null,
	token varchar(32) not null,
	sendAttemptNumber integer not null,
	foreign key (validationSession) references threepid_validations(id)
);
