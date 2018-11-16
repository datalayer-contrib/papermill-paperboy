import configparser
import json
import os
import os.path
import jinja2
import subprocess
from base64 import b64encode
from random import choice
from sqlalchemy import create_engine
from .base import BaseScheduler, TIMING_MAP

with open(os.path.abspath(os.path.join(os.path.dirname(__file__), 'paperboy.airflow.py')), 'r') as fp:
    TEMPLATE = fp.read()

QUERY = '''
SELECT task_id, dag_id, execution_date, state, unixname
FROM task_instance
ORDER BY execution_date ASC
LIMIT 20;
'''

#######################################
#  FIXME merge with dummy when        #
#  airflow has better python3 support #
#######################################


class AirflowScheduler(BaseScheduler):
    def __init__(self, *args, **kwargs):
        super(AirflowScheduler, self).__init__(*args, **kwargs)
        cp = configparser.ConfigParser()
        cp.read(self.config.scheduler.config)
        try:
            self.sql_conn = cp['core']['sql_alchemy_conn']
        except KeyError:
            self.sql_conn = ''

        if self.sql_conn:
            self.engine = create_engine(self.sql_conn)

    def status(self, user, params, session, *args, **kwargs):
        type = params.get('type', '')
        if not self.sql_conn:
            gen = AirflowScheduler.fakequery()
            if type == 'jobs':
                return gen['jobs']
            elif type == 'reports':
                return gen['reports']
            else:
                return gen
        gen = AirflowScheduler.query(self.engine)
        if type == 'jobs':
            return gen['jobs']
        elif type == 'reports':
            return gen['reports']
        else:
            return gen

    @staticmethod
    def query(engine):
        ret = {'jobs': [], 'reports': []}
        with engine.begin() as conn:
            res = conn.execute(QUERY)
            for i, item in enumerate(res):
                ret['jobs'].append(
                    {'name': item[1],
                     'id': item[1][4:],
                     'meta': {
                        'id':  item[1][4:],
                        'execution': item[2].strftime('%m/%d/%Y %H:%M:%S'),
                        'status': '✔' if item[3] == 'success' else '✘'}
                     }
                )

                report_name = item[0].replace('ReportPost-', '') \
                                     .replace('Report-', '') \
                                     .replace('ReportNBConvert-', '') \
                                     .replace('ReportPapermill-', '')

                report_type = 'Post' if 'ReportPost' in item[0] else \
                              'Papermill' if 'Papermill' in item[0] else \
                              'NBConvert' if 'NBConvert' in item[0] else \
                              'Setup'

                ret['reports'].append(
                    {'name': item[0],
                     'id': report_name,
                     'meta': {
                        'run': item[2].strftime('%m/%d/%Y %H:%M:%S'),
                        'status': '✔' if item[3] == 'success' else '✘',
                        'type': report_type
                        }
                     }
                )
            return ret

    @staticmethod
    def fakequery():
        ret = {'jobs': [], 'reports': []}
        for i in range(10):
            ret['jobs'].append(
                {'name': 'DAG-Job-{}'.format(i),
                 'id': 'Job-{}'.format(i),
                 'meta': {
                    'id':  'Job-{}'.format(i),
                    'execution': '01/02/2018 12:25:31',
                    'status': choice(['✔', '✘'])}
                 }
            )
            ret['reports'].append(
                {'name': 'Report-{}'.format(i),
                 'id': 'Report-{}'.format(i),
                 'meta': {
                    'run': '01/02/2018 12:25:31',
                    'status': choice(['✔', '✘']),
                    'type': choice(['Post', 'Papermill', 'NBConvert', 'Setup']),
                    }
                 }
            )
        return ret

    @staticmethod
    def template(config, user, notebook, job, reports, *args, **kwargs):
        owner = user.name
        start_date = job.meta.start_time.strftime('%m/%d/%Y %H:%M:%S')
        email = 'test@test.com'
        job_json = b64encode(json.dumps(job.to_json(True)).encode('utf-8'))
        report_json = b64encode(json.dumps([r.to_json() for r in reports]).encode('utf-8'))
        interval = TIMING_MAP.get(job.meta.interval)

        tpl = jinja2.Template(TEMPLATE).render(
            owner=owner,
            start_date=start_date,
            interval=interval,
            email=email,
            job_json=job_json,
            report_json=report_json,
            output_config=json.dumps(config.output.to_json())
            )
        return tpl

    def schedule(self, user, notebook, job, reports, *args, **kwargs):
        template = AirflowScheduler.template(self.config, user, notebook, job, reports, *args, **kwargs)
        name = job.id + '.py'
        with open(os.path.join(self.config.scheduler.dagbag, name), 'w') as fp:
            fp.write(template)
        return template

    def unschedule(self, user, notebook, job, reports, *args, **kwargs):
        if reports:
            # reschedule
            return self.schedule(user, notebook, job, reports, *args, **kwargs)

        else:
            # delete
            name = job.id + '.py'
            file = os.path.join(self.config.scheduler.dagbag, name)
            dag = 'DAG-' + job.id

            # delete dag file
            os.remove(file)

            # delete dag
            cmd = ['airflow', 'delete_dag', dag, '-y']
            subprocess.call(cmd)
