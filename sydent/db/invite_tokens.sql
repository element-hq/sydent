/*
Copyright 2025 New Vector Ltd.
Copyright 2015 OpenMarket Ltd
Copyright 2019 New Vector Ltd

SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Element-Commercial
Please see LICENSE files in the repository root for full details.

Originally licensed under the Apache License, Version 2.0:
<http://www.apache.org/licenses/LICENSE-2.0>.
*/

-- Note that this SQL file is not up to date, and migrations can be found in sydent/db/sqlitedb.py

CREATE TABLE IF NOT EXISTS invite_tokens (
    id integer primary key,
    medium varchar(16) not null,
    address varchar(256) not null,
    room_id varchar(256) not null,
    sender varchar(256) not null,
    token varchar(256) not null,
    received_ts bigint, -- When the invite was received by us from the homeserver
    sent_ts bigint, -- When the token was sent by us to the user
    origin_id integer, -- original id in homeserver's DB that this was replicated from (if applicable)
    origin_server text -- homeserver this was replicated from (if applicable)
);
CREATE INDEX IF NOT EXISTS invite_token_medium_address on invite_tokens(medium, address);
CREATE INDEX IF NOT EXISTS invite_token_token on invite_tokens(token);

CREATE TABLE IF NOT EXISTS ephemeral_public_keys(
    id integer primary key,
    public_key varchar(256) not null,
    verify_count bigint default 0,
    persistence_ts bigint,
    origin_server text, -- homeserver this was replicated from (if applicable)
    origin_id integer -- original id in homeserver's DB that this was replicated from (if applicable)
);

CREATE UNIQUE INDEX IF NOT EXISTS ephemeral_public_keys_index on ephemeral_public_keys(public_key);
