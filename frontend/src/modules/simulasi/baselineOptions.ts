
import type {
  Baseline,
  Parameters,
  Situation,
} from './types';

export type FieldOption = {
  value: string;
  label: string;
};

const PROJECT_FIELDS = new Set([
  'project_id',
  'origin_project_id',
  'destination_project_id',
]);

const RESOURCE_FIELDS = new Set([
  'resource_id',
  'mentor_resource_id',
]);

const ROLE_FIELDS = new Set([
  'role_id',
  'required_role_id',
]);

const TASK_FIELDS = new Set([
  'task_id',
  'blocking_task_id',
  'affected_task_ids',
  'removed_task_ids',
  'independent_task_ids',
]);

export function getBaselineOptions(
  key: string,
  baseline: Baseline,
  situation: Situation,
  params: Parameters,
): FieldOption[] | null {
  // Projects
  if (PROJECT_FIELDS.has(key)) {
    let projects = baseline.projects;

    if (key === 'destination_project_id') {
      projects = projects.filter(
        (project) =>
          project.project_id !== params.origin_project_id &&
          project.status !== 'released',
      );
    }

    if (
      key === 'project_id' &&
      situation === 'priority_change'
    ) {
      projects = projects.filter(
        (project) => project.status !== 'released',
      );
    }

    return projects.map((project) => ({
      value: project.project_id,
      label: `${project.project_name} (${project.project_id})`,
    }));
  }

  // Resources
  if (RESOURCE_FIELDS.has(key)) {
    return baseline.resources.map((resource) => ({
      value: resource.resource_id,
      label: `${resource.resource_name} (${resource.primary_role_id})`,
    }));
  }

  // Roles
  if (ROLE_FIELDS.has(key)) {
    return baseline.roles.map((role) => ({
      value: role.role_id,
      label: role.role_name,
    }));
  }

  // Tasks
  if (TASK_FIELDS.has(key)) {
    let tasks = baseline.tasks;

    const allowClosed =
      situation === 'critical_bug' ||
      key === 'blocking_task_id';

    if (!allowClosed) {
      tasks = tasks.filter(
        (task) =>
          task.status !== 'done' &&
          task.status !== 'dropped',
      );
    }

    if (
      key === 'removed_task_ids' ||
      (
        key === 'affected_task_ids' &&
        situation === 'priority_change'
      )
    ) {
      tasks = tasks.filter(
        (task) => task.project_id === params.project_id,
      );
    }

    if (
      key === 'affected_task_ids' &&
      situation === 'member_absence'
    ) {
      const assignedIds = new Set(
        baseline.assignments
          .filter(
            (assignment) =>
              assignment.resource_id === params.resource_id,
          )
          .map((assignment) => assignment.task_id),
      );

      tasks = tasks.filter(
        (task) => assignedIds.has(task.task_id),
      );
    }

    if (key === 'blocking_task_id') {
      const dependencyIds = new Set(
        baseline.task_dependencies
          .filter(
            (dependency) =>
              dependency.task_id === params.task_id,
          )
          .map((dependency) => dependency.depends_on_task_id),
      );

      tasks = tasks.filter(
        (task) => dependencyIds.has(task.task_id),
      );
    }

    if (key === 'independent_task_ids') {
      const blocked = new Set<string>();
      const queue: string[] = [];

      if (typeof params.task_id === 'string') {
        queue.push(params.task_id);
      }

      while (queue.length) {
        const current = queue.shift()!;

        if (blocked.has(current)) continue;

        blocked.add(current);

        for (const dependency of baseline.task_dependencies) {
          if (dependency.depends_on_task_id === current) {
            queue.push(dependency.task_id);
          }
        }
      }

      tasks = tasks.filter(
        (task) => !blocked.has(task.task_id),
      );
    }

    return tasks.map((task) => ({
      value: task.task_id,
      label: `${task.task_id} · ${task.task_name}`,
    }));
  }

  // Existing assignments for member rotation
  if (key === 'existing_assignment_ids') {
    return baseline.assignments
      .filter((assignment) => {
        const task = baseline.tasks.find(
          (item) => item.task_id === assignment.task_id,
        );

        const matchesMember =
          assignment.resource_id === params.resource_id;

        const matchesProject =
          task?.project_id === params.origin_project_id;

        const isAvailable =
          !assignment.released_date ||
          typeof params.transfer_date !== 'string' ||
          assignment.released_date >= params.transfer_date;

        return matchesMember && matchesProject && isAvailable;
      })
      .map((assignment) => {
        const task = baseline.tasks.find(
          (item) => item.task_id === assignment.task_id,
        );

        return {
          value: assignment.assignment_id,
          label: `${assignment.assignment_id} · ${task?.task_name ?? assignment.task_id}`,
        };
      });
  }

  // Not a baseline entity field.
  return null;
}
