/**
 * The Node SDK, against a real server rather than a mock.
 *
 *   MP_BASE_URL=http://127.0.0.1:8765 node --test sdk/node/
 *
 * A mocked SDK test proves the SDK agrees with the author's idea of the API,
 * which is the thing most likely to be wrong. So these need a running
 * server: start one with `multiprofile --server-only` and point MP_BASE_URL
 * at it, or they skip.
 *
 * Driven from `tests/test_sdk_node.py` in CI, which starts the server, sets
 * the variable and runs this file -- so the Node client is covered by the
 * same `pytest` invocation as everything else rather than needing a separate
 * job someone forgets to add.
 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';

import { ApiError, Client, LaunchBlocked, MultiprofileError } from './multiprofile.mjs';

const BASE_URL = process.env.MP_BASE_URL;
const created = [];
let mp;

describe('multiprofile node sdk', { skip: BASE_URL ? false : 'MP_BASE_URL is not set' }, () => {
  before(() => {
    mp = new Client({ baseUrl: BASE_URL });
  });

  after(async () => {
    for (const id of created) {
      try {
        await mp.delete(id, { permanent: true });
      } catch {
        // cleanup is best effort
      }
    }
  });

  it('reads health without a token', async () => {
    const body = await mp.health();
    assert.equal(body.ok, true);
    assert.ok(body.version);
  });

  it('reaches the doctor report', async () => {
    const report = await mp.doctor();
    assert.ok('ok' in report);
    assert.ok(Array.isArray(report.findings));
  });

  it('creates, gets and lists', async () => {
    const profile = await mp.create('node-sdk-one', { startUrl: 'about:blank' });
    created.push(profile.id);
    assert.match(profile.id, /^profile_/);

    const fetched = await mp.get(profile.id);
    assert.equal(fetched.id, profile.id);
    // The detail endpoint carries what the list does not.
    assert.ok('identity' in fetched);

    const all = await mp.list();
    assert.ok(all.some((p) => p.id === profile.id));
  });

  it('passes extra fields through', async () => {
    const profile = await mp.create('node-sdk-proxied', {
      proxyType: 'http', proxyHost: '127.0.0.1', proxyPort: 8080,
    });
    created.push(profile.id);
    assert.equal(profile.proxyType, 'http');
  });

  it('turns an unknown field into an error, not a silent setting', async () => {
    // F8: a typo should be a 400, not a stored value that does nothing.
    await assert.rejects(
      () => mp.create('node-sdk-typo', { proxyTpe: 'http' }),
      (error) => error instanceof ApiError && error.status === 400,
    );
  });

  it('reports a permanent delete as an outcome, not a request', async () => {
    // F51: purgeStorage is what happened.
    const profile = await mp.create('node-sdk-permanent');
    const body = await mp.delete(profile.id, { permanent: true });
    assert.equal(body.permanent, true);
    assert.ok('purgeStorage' in body);
    assert.ok('purgeRequested' in body);
  });

  it('raises LaunchBlocked for a refusal, not ApiError', async () => {
    // Authenticated SOCKS5 (F56): Chromium cannot use it, so the launch is
    // refused with 409 and that is a decision, not a fault.
    const profile = await mp.create('node-sdk-blocked', {
      proxyType: 'socks5', proxyHost: '127.0.0.1', proxyPort: 1080,
      proxyUser: 'u', proxyPass: 'p',
    });
    created.push(profile.id);
    await assert.rejects(
      () => mp.launch(profile.id),
      (error) => error instanceof LaunchBlocked && /SOCKS5/.test(error.reason),
    );
  });

  it('says something useful when the server is unreachable', async () => {
    const dead = new Client({ baseUrl: 'http://127.0.0.1:1', timeoutMs: 2000 });
    await assert.rejects(
      () => dead.health(),
      (error) => error instanceof MultiprofileError && /server-only/.test(error.message),
    );
  });

  it('refuses to wait for a session with no CDP port', async () => {
    const session = { profileId: 'profile_x', cdpUrl: null, cdpPort: null };
    await assert.rejects(
      () => mp.waitForCdp(session, { timeoutMs: 500 }),
      (error) => /no CDP port/.test(error.message),
    );
  });
});
