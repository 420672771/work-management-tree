"""One server-side visibility policy for task lists, details, drafts and totals."""
import json


class Scope:
    def __init__(self, c, actor_id, api, *, unrestricted_demo=False):
        self.c, self.api = c, api
        self.actor_id = int(actor_id) if actor_id else None
        actor = c.execute('SELECT role FROM user WHERE id=?', (self.actor_id,)).fetchone() if self.actor_id else None
        self.management = bool(actor and actor['role'] in ('manager', 'pm'))
        # Only the explicitly enabled, unauthenticated demo keeps its legacy
        # all-data fixture. Real login sessions always determine the scope.
        self.all = self.management or (unrestricted_demo and not self.actor_id)
        self.owners = {self.actor_id} if actor else set()
        if actor and not self.management:
            team = api.managed_user_ids(c, self.actor_id)
            self.owners.update(row['id'] for row in c.execute("SELECT id FROM user WHERE role NOT IN ('manager','pm')") if row['id'] in team)
        self._tasks = None

    def owns(self, owner_id):
        try:
            return self.all or bool(owner_id and int(owner_id) in self.owners)
        except (TypeError, ValueError):
            return False

    def approval(self, row):
        if self.all:
            return True
        if not self.actor_id:
            return False
        task = self.c.execute('SELECT owner_id FROM task WHERE id=?', (row['task_id'],)).fetchone()
        return bool((task and self.owns(task['owner_id'])) or self.actor_id in self.api.approval_actor_ids(self.c, row))

    def task_ids(self):
        if self._tasks is None:
            self._tasks = set()
            for row in self.c.execute('SELECT id,owner_id,approver_id FROM task'):
                if self.owns(row['owner_id']) or (self.actor_id and row['approver_id'] == self.actor_id):
                    self._tasks.add(row['id'])
            for row in self.c.execute("SELECT * FROM approval_record WHERE status='pending'"):
                if self.approval(row) and row['task_id']:
                    self._tasks.add(row['task_id'])
        return self._tasks

    def task(self, row):
        return self.all or row['id'] in self.task_ids()

    @staticmethod
    def slots(row):
        try:
            return json.loads(row['slots_json'] or '{}')
        except (ValueError, TypeError):
            return {}

    def draft(self, row):
        slots = self.slots(row)
        return self.all or self.owns(row['user_id']) or self.owns(slots.get('owner_id'))

    def edit_draft(self, row):
        return self.all or bool(self.actor_id and (row['user_id'] == self.actor_id or str(self.slots(row).get('owner_id')) == str(self.actor_id)))

    def duplicates(self, matches):
        visible = {'task': self.task, 'draft': self.draft}
        tables = {'task': 'task', 'draft': 'conversation_draft'}
        result = {}
        for kind, items in matches.items():
            result[kind] = []
            for item in items:
                source = item.get('source', 'task')
                row = self.c.execute('SELECT * FROM ' + tables[source] + ' WHERE id=?', (item['id'],)).fetchone()
                if row and visible[source](row):
                    result[kind].append(item)
        return result
