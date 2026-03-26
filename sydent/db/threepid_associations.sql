/*
Copyright 2025 New Vector Ltd.
Copyright 2014-2017 OpenMarket Ltd

SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
Please see LICENSE files in the repository root for full details.

Originally licensed under the Apache License, Version 2.0:
<http://www.apache.org/licenses/LICENSE-2.0>.
*/

-- Note that this SQL file is not up to date, and migrations can be found in sydent/db/sqlitedb.py

CREATE TABLE IF NOT EXISTS local_threepid_associations (
    id integer primary key,
    medium varchar(16) not null,
    address varchar(256) not null,
    mxid varchar(256) not null,
    ts integer not null,
    notBefore bigint not null,
    notAfter bigint not null
);

CREATE TABLE IF NOT EXISTS global_threepid_associations (
    id integer primary key,
    medium varchar(16) not null,
    address varchar(256) not null,
    mxid varchar(256) not null,
    ts integer not null,
    notBefore bigint not null,
    notAfter integer not null,
    originServer varchar(255) not null,
    originId integer not null,
    sgAssoc text not null
);
CREATE UNIQUE INDEX IF NOT EXISTS originServer_originId on global_threepid_associations (originServer, originId);
