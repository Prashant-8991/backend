-- ############################ Dashboard Data Function #########################
create or REPLACE FUNCTION get_dashboard_data () returns json as $$
declare
   output_json json;
begin
    SELECT
    json_build_object(
        'total_cattle',
        (
            SELECT
                count(*)
            FROM
                cattle_data
            WHERE
                new_is_currently_present = 1
        ),
        'all_cattle_data',
        (
            select count(*) from cattle_data
        ),
        'total_bull', (
            SELECT count(*) FROM cattle_data WHERE animal_type = 'BULL'
        ),
        'total_ox',
        (
            SELECT count(*) FROM cattle_data WHERE animal_type = 'OX'
        ),
        'total_female_cattle',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.gender) = 'female'
        ),
        'total_male_cattle',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.gender) = 'male'
        ),
        'total_female_calf',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.animal_type) = 'female calf'
        ),
        'total_male_calf',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.animal_type) = 'male calf'
        ),
        'total_milking_cow',
        (
            SELECT
                count(*)
            FROM
                cattle_data cd
            WHERE
                cd.new_is_currenlty_milking = 1
                AND cd.new_is_currently_present = 1
        ),
        'total_pregnant_cow',
        (
            SELECT
                count(*)
            FROM
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                AND cd.new_is_currently_pregnant = 1
        ),
        'source_breakdown',
        (
            SELECT
                json_agg(source_breakdown) as source_breakdown
            FROM
                (
                    SELECT
                        acquisition_type,
                        count(*) as total_cattle
                    FROM
                        cattle_data
                    WHERE
                        new_is_currently_present = 1
                    GROUP BY
                        acquisition_type
                    ORDER BY
                        total_cattle desc
                ) as source_breakdown
        ),
        'generation',
        (
            SELECT
                json_agg(gen)
            FROM
                (
                    select
                        sub.generation_level as generation,
                        count(*) as total_cattle
                    FROM
                        (
                            WITH RECURSIVE
                                cattle_lineage AS (
                                    -- 1. Anchor Member
                                    SELECT
                                        cd.*,
                                        1 AS generation_level
                                    FROM
                                        cattle_data cd
                                    WHERE
                                        cd.mother_tag_number IS NULL
                                        AND cd.father_tag_number IS NULL
                                    UNION ALL
                                    -- 2. Recursive Member
                                    SELECT
                                        child.*,
                                        parent.generation_level + 1 AS generation_level
                                    FROM
                                        cattle_data child
                                        INNER JOIN cattle_lineage parent ON child.mother_tag_number = parent.tag_number
                                )
                            SELECT
                                *
                            FROM
                                cattle_lineage
                        ) sub
                    GROUP by
                        sub.generation_level
                    ORDER by
                        sub.generation_level asc
                ) as gen
        ),
        'top_10_milking_cattle',
        (
            select
                json_agg(top_10)
            from
                (
                    WITH RECURSIVE
                        cattle_lineage AS (
                            -- 1. Anchor Member
                            SELECT
                                cd.*,
                                1 AS generation_level
                            FROM
                                cattle_data cd
                            WHERE
                                cd.mother_tag_number IS NULL
                                AND cd.father_tag_number IS NULL
                            UNION ALL
                            -- 2. Recursive Member
                            SELECT
                                child.*,
                                parent.generation_level + 1 AS generation_level
                            FROM
                                cattle_data child
                                INNER JOIN cattle_lineage parent ON child.mother_tag_number = parent.tag_number
                        )
                    SELECT
                        cl.tag_number,
                        cl.name,
                        cl.generation_level AS generation,
                        round(top_milk.total_milk) as "total_milk"
                    FROM
                        cattle_lineage AS cl
                        INNER JOIN (
                            -- Your top 10 milk subquery
                            SELECT
                                cml.tag_number,
                                SUM(cml.milk) AS total_milk
                            FROM
                                public.cattle_milk_logs AS cml
                            GROUP BY
                                cml.tag_number
                            ORDER BY
                                total_milk DESC
                            LIMIT
                                10
                        ) AS top_milk ON cl.tag_number = top_milk.tag_number
                    ORDER BY
                        top_milk.total_milk DESC
                ) as top_10
        ),
        'top_10_fit_cattle',
        (
            select
                json_agg(physical_good)
            from
                (
                    WITH RECURSIVE
                        cattle_lineage AS (
                            -- 1. Anchor Member (The First Generation)
                            SELECT
                                cd.*,
                                1 AS generation_level
                            FROM
                                cattle_data cd
                            WHERE
                                cd.mother_tag_number IS NULL
                                AND cd.father_tag_number IS NULL
                            UNION ALL
                            -- 2. Recursive Member
                            SELECT
                                child.*,
                                parent.generation_level + 1 AS generation_level
                            FROM
                                cattle_data child
                                INNER JOIN cattle_lineage parent ON child.mother_tag_number = parent.tag_number
                        )
                    SELECT
                        cl.tag_number,
                        cl.name,
                        cl.generation_level AS generation,
                        top_scores.hip_width,
                        top_scores.total_score
                    FROM
                        cattle_lineage AS cl
                        INNER JOIN (
                            -- Your top 10 scoring subquery
                            SELECT
                                tag_number,
                                hip_width,
                                (
                                    ROUND(
                                        (
                                            COALESCE(head, 0) +
                                            COALESCE(ear, 0) +
                                            COALESCE(eye, 0) +
                                            COALESCE(muzzle, 0) +
                                            COALESCE(horn, 0) +
                                            COALESCE(skin, 0) +
                                            COALESCE(tail, 0) +
                                            COALESCE(hump, 0) +
                                            COALESCE(udder, 0) +
                                            COALESCE(teat, 0) +
                                            COALESCE(dewlap, 0) +
                                            COALESCE(milk_vein, 0)
                                        )::numeric / 12,
                                        3
                                    )
                                ) AS total_score
                            FROM
                                cattle_physical_logs
                            ORDER BY
                                total_score DESC
                            LIMIT
                                10
                        ) AS top_scores ON cl.tag_number = top_scores.tag_number
                    ORDER BY
                        top_scores.total_score DESC
                ) physical_good
        ),
        'month_wise_milk_production',
        (
            select json_agg(monthwisemilk) as monthwisemilk
            from (
                SELECT 
                    TO_CHAR(date, 'YYYY-MM') AS month,
                    round(SUM(milk)) AS total_milk
                FROM 
                    cattle_milk_logs
                GROUP BY 
                    TO_CHAR(date, 'YYYY-MM')
                ORDER BY 
                    month ASC
            ) as monthwisemilk
        ),
        'average_milk_by_per_cattle', (
            select
            json_build_object(
                'average_milk_by_per_cattle',
                (round(avg(sub.average)))
            )
            from
                (
                    SELECT
                        tag_number,
                        round(AVG(milk)) as "average"
                    from
                        cattle_milk_logs
                    GROUP BY
                        tag_number
                    order by
                        average desc
                ) as sub
        )
    ) into output_json;

    return output_json;

end;
$$ language plpgsql;

drop FUNCTION get_dashboard_data;

select
    *
from
    get_dashboard_data ();

select
    *
from
    get_dashboard_data ();

-- drop function get_dashboard_data();
-- ############################ cattle nested tree function #########################
CREATE OR REPLACE FUNCTION get_nested_cattle_tree (target_tag TEXT) RETURNS JSON AS $$
DECLARE
    tree_json JSON;
BEGIN
    SELECT json_build_object(
        'tag_number', c.tag_number,
        'name', c.name,
        'date_of_birth', c.date_of_birth,
        -- The recursive part: It calls itself to find and nest the next generation
        'children', (
            SELECT COALESCE(json_agg(get_nested_cattle_tree(child.tag_number)), '[]'::json)
            FROM cattle_data child
            WHERE child.mother_tag_number = c.tag_number 
               OR child.father_tag_number = c.tag_number
        )
    ) INTO tree_json
    FROM cattle_data c
    WHERE c.tag_number = target_tag;

    RETURN tree_json;
END;
$$ LANGUAGE plpgsql;

-- drop function get_nested_cattle_tree();
-- ########################## get all present cattle function ##########################
create or REPLACE FUNCTION get_all_present_cattle () returns JSON AS $$
declare
    json_data json;
begin
    select
        json_agg(present_cattle)
    from
        (
            select
                tag_number,
                name,
                gender,
                acquisition_type,
                animal_type,
                new_is_currenlty_milking as is_milking,
                new_is_currently_pregnant as is_pregnant
            from
                cattle_data
            where
                new_is_currently_present = 1
        ) as present_cattle into json_data;
    return json_data;
end;
$$ language plpgsql;

drop function get_all_present_cattle ();

-- ########################## get all milking cattle function ##########################
create or REPLACE FUNCTION get_all_milking_cattle () returns JSON AS $$
declare
    json_data json;
begin
    select
        json_agg(present_cattle)
    from
        (
            select
                tag_number,
                name,
                gender,
                acquisition_type,
                animal_type,
                new_is_currenlty_milking as is_milking,
                new_is_currently_pregnant as is_pregnant
            from
                cattle_data
            where
                new_is_currently_present = 1
                and new_is_currenlty_milking = 1
        ) as present_cattle into json_data;
    return json_data;
end;
$$ language plpgsql;

-- drop function get_all_milking_cattle();
-- ########## cattle profile function ##########
create or replace function get_cattle_profile (target_tag text) returns json as $$
declare
    profile_json json;
begin
    with recursive cattle_lineage as (
        select
            tag_number,
            mother_tag_number,
            father_tag_number,
            name,
            date_of_birth,
            new_is_currently_present,
            1 as generation_level
        from cattle_data
        where mother_tag_number is null and father_tag_number is null
        union all
        select
            child.tag_number,
            child.mother_tag_number,
            child.father_tag_number,
            child.name,
            child.date_of_birth,
            child.new_is_currently_present,
            parent.generation_level + 1
        from cattle_data child
        inner join cattle_lineage parent on child.mother_tag_number = parent.tag_number
    ),
    overview_cte as (
        select
            cl.name,
            cl.tag_number,
            cl.generation_level as generation,
            cl.date_of_birth,
            cl.new_is_currently_present as is_present,
            (select count(*) from cattle_data child where child.mother_tag_number = cl.tag_number) as total_number_of_children,
            (select count(*) from cattle_data sibling where sibling.mother_tag_number = cl.mother_tag_number and sibling.tag_number != cl.tag_number) as total_number_of_siblings,
            (select name from cattle_data where tag_number = cl.mother_tag_number) as mother_name,
            (select name from cattle_data where tag_number = cl.father_tag_number) as father_name
        from cattle_lineage cl
        where cl.tag_number = target_tag
    ),
    children_cte as (
        select
            cl.name,
            cl.tag_number,
            cl.generation_level as generation,
            cl.date_of_birth,
            (select count(*) from cattle_data grandchild where grandchild.mother_tag_number = cl.tag_number) as total_number_of_children,
            (select count(*) from cattle_data sibling where sibling.mother_tag_number = cl.mother_tag_number and sibling.tag_number != cl.tag_number) as total_number_of_siblings,
            (select name from cattle_data where tag_number = cl.mother_tag_number) as mother_name,
            (select name from cattle_data where tag_number = cl.father_tag_number) as father_name
        from cattle_lineage cl
        where cl.mother_tag_number = target_tag or cl.father_tag_number = target_tag
    ),
    physical_cte as (
        select
            hip_width,
            head as head_score,
            ear as ear_score,
            eye as eye_score,
            muzzle as muzzle_score,
            horn as horn_score,
            skin as skin_score,
            tail as tail_score,
            hump as hump_score,
            udder as udder_score,
            teat as teat_score,
            dewlap as dewlap_score,
            milk_vein as milk_vein_score
        from cattle_physical_logs
        where tag_number = target_tag
        -- order by date desc
        limit 1
    ),
    milk_cte as (
        select
            to_char(date, 'YYYY-MM') as month,
            round(sum(milk)::numeric) as milk
        from cattle_milk_logs
        where tag_number = target_tag
        group by to_char(date, 'YYYY-MM')
        order by month asc
    ),
    siblings_cte as (
        select
            s.name,
            s.tag_number,
            s.date_of_birth
        from cattle_data s
        where s.mother_tag_number = (select mother_tag_number from cattle_data where tag_number = target_tag)
          and s.tag_number != target_tag
    ),
    family_cte as (
        select
            (select name from cattle_data where tag_number = (select mother_tag_number from cattle_data where tag_number = target_tag)) as mother_name,
            (select name from cattle_data where tag_number = (select father_tag_number from cattle_data where tag_number = target_tag)) as father_name,
            (select name from cattle_data where tag_number = (select mother_tag_number from cattle_data where tag_number = (select mother_tag_number from cattle_data where tag_number = target_tag))) as grand_mother_name,
            (select name from cattle_data where tag_number = (select father_tag_number from cattle_data where tag_number = (select mother_tag_number from cattle_data where tag_number = target_tag))) as grand_father_name
    )
    select json_build_object(
        'overview', (
            select json_build_object(
                'name', o.name,
                'tag_number', o.tag_number,
                'generation', o.generation,
                'date_of_birth', o.date_of_birth,
                'is_present', o.is_present,
                'total_number_of_children', o.total_number_of_children,
                'total_number_of_siblings', o.total_number_of_siblings,
                'mother_name', o.mother_name,
                'father_name', o.father_name,
                'children', coalesce((select json_agg(json_build_object(
                    'name', c.name,
                    'tag_number', c.tag_number,
                    'generation', c.generation,
                    'date_of_birth', c.date_of_birth,
                    'total_number_of_children', c.total_number_of_children,
                    'total_number_of_siblings', c.total_number_of_siblings,
                    'mother_name', c.mother_name,
                    'father_name', c.father_name
                )) from children_cte c), '[]'::json),
                'physical_data', (
                    select row_to_json(p.*) from physical_cte p
                )
            ) from overview_cte o
        ),
        'milk_logs', coalesce((select json_agg(json_build_object(
            'month', m.month,
            'milk', m.milk
        )) from milk_cte m), '[]'::json),
        'family_tree', (
            select json_build_object(
                'mother_name', f.mother_name,
                'father_name', f.father_name,
                'grand_mother_name', f.grand_mother_name,
                'grand_father_name', f.grand_father_name,
                'siblings', coalesce((select json_agg(json_build_object(
                    'name', s.name,
                    'tag_number', s.tag_number,
                    'date_of_birth', s.date_of_birth
                )) from siblings_cte s), '[]'::json)
            ) from family_cte f
        )
    ) into profile_json;

    return profile_json;
end;
$$ language plpgsql;

-- drop function get_cattle_profile();
-- ########## genealogy all cattle ##########
-- ########################## get donated cattle function (incoming + outgoing) ##########################
create or replace function get_donated_cattle () returns json as $$
declare
    json_data json;
begin
    select
        json_agg(sub) into json_data
    from (
        with
            donated_out_cte as (
                select
                    name,
                    tag_number,
                    donated_out_date::text as donated_date,
                    donated_to as donated,
                    mobile_number,
                    gender,
                    'outgoing' as out_type
                from donated_out
            ),
            donated_in_cte as (
                select distinct on (tag_number)
                    name,
                    tag_number,
                    donated_in_date::text as donated_date,
                    from_donated_in as donated,
                    '' as mobile_number,
                    gender,
                    'incoming' as out_type
                from donated_in
                order by tag_number, donated_in_date desc nulls last
            )
        select *
        from donated_out_cte
        union all
        select *
        from donated_in_cte
        order by donated_date desc nulls last
    ) as sub;
    return json_data;
end;
$$ language plpgsql;

-- drop function get_donated_out_cattle();
-- ########## genealogy all cattle ##########
create or replace function get_all_cattle_for_genealogy () returns json as $$
declare
    json_data json;
begin
    with recursive cattle_lineage as (
        select
            cd.*,
            1 as generation_level
        from cattle_data cd
        where cd.mother_tag_number is null
        union all
        select
            child.*,
            parent.generation_level + 1 as generation_level
        from cattle_data child
        inner join cattle_lineage parent on child.mother_tag_number = parent.tag_number
    )
    select
        json_agg(genealogy_data)
    from (
        select
            tag_number,
            name,
            gender,
            animal_type,
            acquisition_type,
            date_of_birth::text as date_of_birth,
            new_is_currently_present as is_present,
            new_is_currenlty_milking as is_milking,
            new_is_currently_pregnant as is_pregnant,
            mother_tag_number,
            father_tag_number,
            generation_level as generation
        from cattle_lineage
        order by generation_level asc, name asc
    ) as genealogy_data into json_data;
    return json_data;
end;
$$ language plpgsql;

-- ########## cattle card data function ##########
-- create or replace function get_cattle_card_data (target_tag text) returns json as $$
-- declare
--     result_json json;
-- begin
--     with recursive cattle_lineage as (
--         select
--             cd.id, cd.name, cd.tag_number, cd.acquisition_type,
--             cd.date_of_birth, cd.animal_type, cd.mother_name,
--             cd.mother_tag_number, cd.father_name, cd.father_tag_number,
--             cd.new_is_currently_present, cd.new_is_currently_pregnant,
--             cd.new_is_currenlty_milking, cd.weight_at_birth, cd.gender,
--             1::int as generation_level
--         from cattle_data cd
--         where cd.mother_tag_number is null and cd.father_tag_number is null
--         union all
--         select
--             child.id, child.name, child.tag_number, child.acquisition_type,
--             child.date_of_birth, child.animal_type, child.mother_name,
--             child.mother_tag_number, child.father_name, child.father_tag_number,
--             child.new_is_currently_present, child.new_is_currently_pregnant,
--             child.new_is_currenlty_milking, child.weight_at_birth, child.gender,
--             (parent.generation_level + 1)::int as generation_level
--         from cattle_data child
--         inner join cattle_lineage parent on child.mother_tag_number = parent.tag_number
--     ),
--     me as (
--         select * from cattle_lineage where tag_number = target_tag
--     ),
--     phys as (
--         select * from cattle_physical_logs where tag_number = target_tag order by id desc limit 1
--     )
--     select json_build_object(
--         'overview', json_build_object(
--             'name', me.name,
--             'tag_number', me.tag_number,
--             'physical_score', (select round((coalesce(head::numeric,0)+coalesce(ear::numeric,0)+coalesce(eye::numeric,0)+coalesce(muzzle::numeric,0)+coalesce(horn::numeric,0)+coalesce(skin::numeric,0)+coalesce(tail::numeric,0)+coalesce(hump::numeric,0)+coalesce(udder::numeric,0)+coalesce(teat::numeric,0)+coalesce(dewlap::numeric,0)+coalesce(milk_vein::numeric,0))/12.0, 1) from phys),
--             'average_physical_score', (select round((coalesce(avg(head)::numeric,0)+coalesce(avg(ear)::numeric,0)+coalesce(avg(eye)::numeric,0)+coalesce(avg(muzzle)::numeric,0)+coalesce(avg(horn)::numeric,0)+coalesce(avg(skin)::numeric,0)+coalesce(avg(tail)::numeric,0)+coalesce(avg(hump)::numeric,0)+coalesce(avg(udder)::numeric,0)+coalesce(avg(teat)::numeric,0)+coalesce(avg(dewlap)::numeric,0)+coalesce(avg(milk_vein)::numeric,0))/12.0, 1) from cattle_physical_logs),
--             'acquisition_type', coalesce(me.acquisition_type, 'Not available'),
--             'generation', coalesce(me.generation_level::text, 'Not available'),
--             'DOB', coalesce(me.date_of_birth, 'Not available'),
--             'total_childrens', (select count(*) from cattle_data where mother_tag_number = target_tag),
--             'siblings', coalesce((select json_agg(json_build_object('name', sib.name, 'tag_number', sib.tag_number, 'generation', sib.generation_level)) from cattle_lineage sib where sib.mother_tag_number = me.mother_tag_number and sib.tag_number != target_tag), '[]'::json),
--             'is_present', me.new_is_currently_present,
--             'lactation_cycle', case
--                 when me.new_is_currenlty_milking = 1 then 'Lactating'
--                 when me.new_is_currently_pregnant = 1 then 'Pregnant'
--                 when (select birth_date from cattle_pragnancies_logs where tag_number = target_tag and birth_date is not null order by birth_date desc limit 1) is not null then 'Post-lactation'
--                 else 'Not available'
--             end,
--             'last_calving_date', coalesce((select birth_date from cattle_pragnancies_logs where tag_number = target_tag and birth_date is not null order by birth_date desc limit 1), 'Not available'),
--             'mother', case when me.mother_tag_number is not null and (select name from cattle_lineage where tag_number = me.mother_tag_number) is not null then (select json_build_object('name', m.name, 'tag_number', m.tag_number, 'generation', m.generation_level) from cattle_lineage m where m.tag_number = me.mother_tag_number) else to_json('Not available'::text) end,
--             'father', case when me.father_tag_number is not null and (select name from cattle_lineage where tag_number = me.father_tag_number) is not null then (select json_build_object('name', f.name, 'tag_number', f.tag_number, 'generation', f.generation_level) from cattle_lineage f where f.tag_number = me.father_tag_number) else to_json('Not available'::text) end,
--             'childrens', coalesce((select json_agg(json_build_object('name', c.name, 'tag_number', c.tag_number, 'generation', c.generation_level)) from cattle_lineage c where c.mother_tag_number = target_tag), '[]'::json),
--             'breed_score', json_build_object(
--                 'hip_width', coalesce((select hip_width from phys), '0'),
--                 'head', coalesce((select head::text from phys), '0'),
--                 'ear', coalesce((select ear::text from phys), '0'),
--                 'eye', coalesce((select eye::text from phys), '0'),
--                 'muzzle', coalesce((select muzzle::text from phys), '0'),
--                 'horn', coalesce((select horn::text from phys), '0'),
--                 'skin', coalesce((select skin::text from phys), '0'),
--                 'tail', coalesce((select tail::text from phys), '0'),
--                 'hump', coalesce((select hump::text from phys), '0'),
--                 'udder', coalesce((select udder::text from phys), '0'),
--                 'teat', coalesce((select teat::text from phys), '0'),
--                 'dewlap', coalesce((select dewlap::text from phys), '0'),
--                 'milk_vein', coalesce((select milk_vein::text from phys), '0')
--             ),
--             'weight', case when me.weight_at_birth is not null then me.weight_at_birth::text else 'Not available' end,
--             'age', case
--                 when me.date_of_birth is not null and me.date_of_birth ~ '^\d{4}-\d{2}-\d{2}' then
--                     extract(year from age(now(), me.date_of_birth::date))::text || ' years'
--                 else 'Not available'
--             end,
--             'average_milk_per_day', case when me.new_is_currenlty_milking = 1 then (select round(avg(milk)::numeric, 1) from cattle_milk_logs where tag_number = target_tag) else null end
--         ),
--         'milk_by_month', case when me.new_is_currenlty_milking = 1
--             then coalesce((select json_agg(json_build_object('date', month_group, 'milk', total_milk)) from (
--                 select to_char(date::timestamp, 'YYYY-MM') as month_group, round(sum(milk)::numeric, 1) as total_milk
--                 from cattle_milk_logs where tag_number = target_tag
--                 group by to_char(date::timestamp, 'YYYY-MM')
--                 order by month_group
--             ) sub), '[]'::json)
--             else '[]'::json
--         end,
--         'milk_by_day_only_for_month', case when me.new_is_currenlty_milking = 1
--             then coalesce((select json_agg(json_build_object('date', milk_date, 'milk', round(milk::numeric, 1))) from (
--                 select date as milk_date, milk from cattle_milk_logs where tag_number = target_tag order by milk_date desc limit 30
--             ) sub), '[]'::json)
--             else '[]'::json
--         end,
--         'family', json_build_object(
--             'mother', (select case when m.name is not null then json_build_object('name', m.name, 'tag_number', m.tag_number, 'generation', m.generation_level) else null::json end from cattle_lineage m where m.tag_number = me.mother_tag_number),
--             'father', (select case when f.name is not null then json_build_object('name', f.name, 'tag_number', f.tag_number, 'generation', f.generation_level) else null::json end from cattle_lineage f where f.tag_number = me.father_tag_number),
--             'siblings', coalesce((select json_agg(json_build_object('name', sib.name, 'tag_number', sib.tag_number, 'generation', sib.generation_level)) from cattle_lineage sib where sib.mother_tag_number = me.mother_tag_number and sib.tag_number != target_tag), '[]'::json),
--             'childrens', coalesce((select json_agg(json_build_object('name', c.name, 'tag_number', c.tag_number, 'generation', c.generation_level)) from cattle_lineage c where c.mother_tag_number = target_tag), '[]'::json)
--         )
--     ) into result_json
--     from me;

--     return result_json;
-- end;
-- $$ language plpgsql;




create or replace function get_cattle_card_data (target_tag text) returns json as $$
declare
    result_json json;
begin
    with recursive cattle_lineage as (
        select
            cd.id, cd.name, cd.tag_number, cd.acquisition_type,
            cd.date_of_birth, cd.animal_type, cd.mother_name,
            cd.mother_tag_number, cd.father_name, cd.father_tag_number,
            cd.new_is_currently_present, cd.new_is_currently_pregnant,
            cd.new_is_currenlty_milking, cd.weight_at_birth, cd.gender,
            1::int as generation_level
        from cattle_data cd
        where cd.mother_tag_number is null and cd.father_tag_number is null
        union all
        select
            child.id, child.name, child.tag_number, child.acquisition_type,
            child.date_of_birth, child.animal_type, child.mother_name,
            child.mother_tag_number, child.father_name, child.father_tag_number,
            child.new_is_currently_present, child.new_is_currently_pregnant,
            child.new_is_currenlty_milking, child.weight_at_birth, child.gender,
            (parent.generation_level + 1)::int as generation_level
        from cattle_data child
        inner join cattle_lineage parent on child.mother_tag_number = parent.tag_number
    ),
    me as (
        select * from cattle_lineage where tag_number = target_tag
    ),
    phys as (
        select * from cattle_physical_logs where tag_number = target_tag order by id desc limit 1
    ),
    -- --- NEW CTEs: Pregnancy & Calving Interval Calculations ---
    cleaned_dates as (
        select 
            id,
            tag_number,
            case when conception_date in ('1900-01-00', '-', '') then null else conception_date end::timestamp as safe_conception_date,
            case when birth_date in ('1900-01-00', '-', '') then null else birth_date end::timestamp as safe_birth_date
        from cattle_pragnancies_logs
        where tag_number = target_tag
    ),
    previous_births as (
        select 
            id,
            safe_conception_date,
            safe_birth_date,
            lag(safe_birth_date) over (order by safe_birth_date) as previous_birth_date
        from cleaned_dates
    ),
    pregnancy_stats as (
        select
            id,
            safe_conception_date as conception_date,
            safe_birth_date as birth_date,
            -- Casting interval to text ensures clean JSON serialization
            age(safe_birth_date, safe_conception_date)::text as gestation_period,
            age(safe_birth_date, previous_birth_date)::text as calving_interval
        from previous_births
        order by safe_birth_date desc
    )
    -- --- END NEW CTEs ---
    
    select json_build_object(
        'overview', json_build_object(
            'name', me.name,
            'tag_number', me.tag_number,
            'physical_score', (select round((coalesce(head::numeric,0)+coalesce(ear::numeric,0)+coalesce(eye::numeric,0)+coalesce(muzzle::numeric,0)+coalesce(horn::numeric,0)+coalesce(skin::numeric,0)+coalesce(tail::numeric,0)+coalesce(hump::numeric,0)+coalesce(udder::numeric,0)+coalesce(teat::numeric,0)+coalesce(dewlap::numeric,0)+coalesce(milk_vein::numeric,0))/12.0, 1) from phys),
            'average_physical_score', (select round((coalesce(avg(head)::numeric,0)+coalesce(avg(ear)::numeric,0)+coalesce(avg(eye)::numeric,0)+coalesce(avg(muzzle)::numeric,0)+coalesce(avg(horn)::numeric,0)+coalesce(avg(skin)::numeric,0)+coalesce(avg(tail)::numeric,0)+coalesce(avg(hump)::numeric,0)+coalesce(avg(udder)::numeric,0)+coalesce(avg(teat)::numeric,0)+coalesce(avg(dewlap)::numeric,0)+coalesce(avg(milk_vein)::numeric,0))/12.0, 1) from cattle_physical_logs),
            'acquisition_type', coalesce(me.acquisition_type, 'Not available'),
            'generation', coalesce(me.generation_level::text, 'Not available'),
            'DOB', coalesce(me.date_of_birth, 'Not available'),
            'total_childrens', (select count(*) from cattle_data where mother_tag_number = target_tag),
            'siblings', coalesce((select json_agg(json_build_object('name', sib.name, 'tag_number', sib.tag_number, 'generation', sib.generation_level)) from cattle_lineage sib where sib.mother_tag_number = me.mother_tag_number and sib.tag_number != target_tag), '[]'::json),
            'is_present', me.new_is_currently_present,
            'lactation_cycle', case
                when me.new_is_currenlty_milking = 1 then 'Lactating'
                when me.new_is_currently_pregnant = 1 then 'Pregnant'
                when (select birth_date from cattle_pragnancies_logs where tag_number = target_tag and birth_date is not null order by birth_date desc limit 1) is not null then 'Post-lactation'
                else 'Not available'
            end,
            'last_calving_date', coalesce((select birth_date from cattle_pragnancies_logs where tag_number = target_tag and birth_date is not null order by birth_date desc limit 1), 'Not available'),
            'mother', case when me.mother_tag_number is not null and (select name from cattle_lineage where tag_number = me.mother_tag_number) is not null then (select json_build_object('name', m.name, 'tag_number', m.tag_number, 'generation', m.generation_level) from cattle_lineage m where m.tag_number = me.mother_tag_number) else to_json('Not available'::text) end,
            'father', case when me.father_tag_number is not null and (select name from cattle_lineage where tag_number = me.father_tag_number) is not null then (select json_build_object('name', f.name, 'tag_number', f.tag_number, 'generation', f.generation_level) from cattle_lineage f where f.tag_number = me.father_tag_number) else to_json('Not available'::text) end,
            'childrens', coalesce((select json_agg(json_build_object('name', c.name, 'tag_number', c.tag_number, 'generation', c.generation_level)) from cattle_lineage c where c.mother_tag_number = target_tag), '[]'::json),
            'breed_score', json_build_object(
                'hip_width', coalesce((select hip_width from phys), '0'),
                'head', coalesce((select head::text from phys), '0'),
                'ear', coalesce((select ear::text from phys), '0'),
                'eye', coalesce((select eye::text from phys), '0'),
                'muzzle', coalesce((select muzzle::text from phys), '0'),
                'horn', coalesce((select horn::text from phys), '0'),
                'skin', coalesce((select skin::text from phys), '0'),
                'tail', coalesce((select tail::text from phys), '0'),
                'hump', coalesce((select hump::text from phys), '0'),
                'udder', coalesce((select udder::text from phys), '0'),
                'teat', coalesce((select teat::text from phys), '0'),
                'dewlap', coalesce((select dewlap::text from phys), '0'),
                'milk_vein', coalesce((select milk_vein::text from phys), '0')
            ),
            'weight', case when me.weight_at_birth is not null then me.weight_at_birth::text else 'Not available' end,
            'age', case
                when me.date_of_birth is not null and me.date_of_birth ~ '^\d{4}-\d{2}-\d{2}' then
                    extract(year from age(now(), me.date_of_birth::date))::text || ' years'
                else 'Not available'
            end,
            'average_milk_per_day', case when me.new_is_currenlty_milking = 1 then (select round(avg(milk)::numeric, 1) from cattle_milk_logs where tag_number = target_tag) else null end,
            'gender', me.gender,
            'animal_type', me.animal_type
        ),
        'milk_by_month', case when me.new_is_currenlty_milking = 1
            then coalesce((select json_agg(json_build_object('date', month_group, 'milk', total_milk)) from (
                select to_char(date::timestamp, 'YYYY-MM') as month_group, round(sum(milk)::numeric, 1) as total_milk
                from cattle_milk_logs where tag_number = target_tag
                group by to_char(date::timestamp, 'YYYY-MM')
                order by month_group
            ) sub), '[]'::json)
            else '[]'::json
        end,
        'milk_by_day_only_for_month', case when me.new_is_currenlty_milking = 1
            then coalesce((select json_agg(json_build_object('date', milk_date, 'milk', round(milk::numeric, 1))) from (
                select date as milk_date, milk from cattle_milk_logs where tag_number = target_tag order by milk_date desc limit 30
            ) sub), '[]'::json)
            else '[]'::json
        end,
        'family', json_build_object(
            'mother', (select case when m.name is not null then json_build_object('name', m.name, 'tag_number', m.tag_number, 'generation', m.generation_level) else null::json end from cattle_lineage m where m.tag_number = me.mother_tag_number),
            'father', (select case when f.name is not null then json_build_object('name', f.name, 'tag_number', f.tag_number, 'generation', f.generation_level) else null::json end from cattle_lineage f where f.tag_number = me.father_tag_number),
            'siblings', coalesce((select json_agg(json_build_object('name', sib.name, 'tag_number', sib.tag_number, 'generation', sib.generation_level)) from cattle_lineage sib where sib.mother_tag_number = me.mother_tag_number and sib.tag_number != target_tag), '[]'::json),
            'childrens', coalesce((select json_agg(json_build_object('name', c.name, 'tag_number', c.tag_number, 'generation', c.generation_level)) from cattle_lineage c where c.mother_tag_number = target_tag), '[]'::json)
        ),
        
        -- --- NEW JSON KEY: pregnancy_logs ---
        'pregnancy_logs', coalesce((
            select json_agg(json_build_object(
                'id', p.id,
                'conception_date', p.conception_date,
                'birth_date', p.birth_date,
                'gestation_period', p.gestation_period,
                'calving_interval', p.calving_interval
            ))
            from pregnancy_stats p
        ), '[]'::json)
        -- --- END NEW JSON KEY ---
        
    ) into result_json
    from me;

    return result_json;
end;
$$ language plpgsql;

select *
from
    get_cattle_card_data ('SOM-052');

CREATE OR REPLACE FUNCTION get_specific_cow_milk_data_for_current_month (p_tag_number TEXT, p_year_month TEXT DEFAULT NULL) RETURNS JSON AS $$
DECLARE
    v_month_start DATE;
    json_data JSON;
BEGIN
    IF p_year_month IS NULL THEN
        v_month_start := date_trunc('month', CURRENT_DATE)::DATE;
    ELSE
        v_month_start := to_date(p_year_month || '-01', 'YYYY-MM-DD');
    END IF;

    SELECT json_agg(data)
    INTO json_data
    FROM (
        SELECT
            cml.tag_number,
            cml.date,
            cml.milk
        FROM cattle_milk_logs cml
        WHERE cml.tag_number = p_tag_number
          AND cml.date >= v_month_start
          AND cml.date < v_month_start + INTERVAL '1 month'
        ORDER BY cml.date
    ) data;

    RETURN COALESCE(json_data, '[]'::json);
END;
$$ LANGUAGE plpgsql;

drop FUNCTION get_specific_cow_milk_data_for_current_month;

SELECT
    get_specific_cow_milk_data_for_current_month ('SOM-052', '2026-05');

ALTER TABLE cattle_milk_logs
ADD CONSTRAINT cattle_milk_logs_tag_date_unique UNIQUE (tag_number, date);

CREATE OR REPLACE FUNCTION insert_cattle_milk_log (p_tag_number TEXT, p_date DATE, p_milk FLOAT8) RETURNS JSON AS $$
DECLARE
    result JSON;
BEGIN
    INSERT INTO cattle_milk_logs (
        tag_number,
        date,
        milk
    )
    VALUES (
        p_tag_number,
        p_date,
        p_milk
    )
    ON CONFLICT (tag_number, date)
    DO UPDATE
    SET milk = EXCLUDED.milk;

    result := json_build_object(
        'success', true,
        'message', 'Milk log saved successfully',
        'tag_number', p_tag_number,
        'date', p_date,
        'milk', p_milk
    );

    RETURN result;
END;
$$ LANGUAGE plpgsql;

-- CREATE OR REPLACE FUNCTION cattle_vaccine () RETURNS json AS $$
-- DECLARE
--     json_data json;
--     v_sixty_days_from_now DATE;
-- BEGIN
--     v_sixty_days_from_now := CURRENT_DATE + 60;

--     WITH
--     latest_vaccinations AS (
--         SELECT DISTINCT ON (cvl.tag_number, cvl.vaccine_id)
--             cvl.tag_number,
--             cvl.vaccine_id,
--             cvl.vaccinated_on AS last_vaccination,
--             (cvl.vaccinated_on + v.booster_after_days::integer)::DATE AS next_date
--         FROM cattle_vaccine_logs cvl
--         JOIN vaccine v ON v.id = cvl.vaccine_id
--         ORDER BY cvl.tag_number, cvl.vaccine_id, cvl.vaccinated_on DESC
--     ),
--     vaccine_master AS (
--         SELECT id, COALESCE(full_name, name) AS name, booster_after_days 
--         FROM vaccine 
--         WHERE id IN (1, 2)
--     ),
--     all_cattle_vaccines AS (
--         SELECT
--             cd.tag_number,
--             cd.name AS cattle_name,
--             vm.id AS vaccine_id,
--             vm.name,
--             vm.booster_after_days,
--             lv.last_vaccination,
--             COALESCE(lv.next_date, CURRENT_DATE) AS next_date
--         FROM cattle_data cd
--         CROSS JOIN vaccine_master vm
--         LEFT JOIN latest_vaccinations lv ON lv.tag_number = cd.tag_number AND lv.vaccine_id = vm.id
--         WHERE cd.new_is_currently_present = 1
--     ),
--     vaccine_1_and_2 AS (
--         SELECT
--             tag_number,
--             cattle_name,
--             name,
--             last_vaccination,
--             next_date,
--             CASE
--                 WHEN last_vaccination IS NULL THEN 'Pending'
--                 WHEN next_date < CURRENT_DATE THEN 'overdue'
--                 WHEN next_date <= v_sixty_days_from_now THEN 'Pending'
--                 ELSE 'Pending'
--             END AS data
--         FROM all_cattle_vaccines
--         WHERE last_vaccination IS NULL
--            OR next_date < CURRENT_DATE
--            OR next_date <= v_sixty_days_from_now
--     ),
--     ),
--     vaccine_3 AS (
--         SELECT
--             cd.tag_number,
--             cd.name AS cattle_name,
--             COALESCE(v.full_name, v.name) AS name,
--             NULL::date AS last_vaccination,
--             NULL::date AS next_date,
--             'Pending'::text AS data
--         FROM cattle_data cd
--         CROSS JOIN vaccine v
--         WHERE cd.new_is_currently_present = 1
--           AND v.id = 3
--           AND LOWER(cd.gender) = 'female'
--           AND (cd.brucellosis_status = 'NOT_VACCINATED' OR cd.brucellosis_status = 'UNKNOWN')
--     )
--     SELECT json_agg(row_to_json(combined_data)) INTO json_data
--     FROM (
--         SELECT tag_number, cattle_name, name, last_vaccination, next_date, data
--         FROM vaccine_1_and_2
--         WHERE data IS NOT NULL
--         UNION ALL
--         SELECT tag_number, cattle_name, name, last_vaccination, next_date, data
--         FROM vaccine_3
--     ) AS combined_data;

--     RETURN json_data;
-- END;
-- $$ LANGUAGE plpgsql;




CREATE OR REPLACE FUNCTION cattle_vaccine()
RETURNS json
AS $$
DECLARE
    json_data json;
    v_sixty_days_from_now DATE;
BEGIN
    v_sixty_days_from_now := CURRENT_DATE + 60;

    WITH latest_vaccinations AS (
        SELECT DISTINCT ON (cvl.tag_number, cvl.vaccine_id)
            cvl.tag_number,
            cvl.vaccine_id,
            cvl.vaccinated_on AS last_vaccination,
            (
                cvl.vaccinated_on +
                v.booster_after_days::integer
            )::DATE AS next_date
        FROM cattle_vaccine_logs cvl
        JOIN vaccine v
            ON v.id = cvl.vaccine_id
        ORDER BY
            cvl.tag_number,
            cvl.vaccine_id,
            cvl.vaccinated_on DESC
    ),

    vaccine_master AS (
        SELECT
            id,
            COALESCE(full_name, name) AS name,
            booster_after_days
        FROM vaccine
        WHERE id IN (1, 2)
    ),

    all_cattle_vaccines AS (
        SELECT
            cd.tag_number,
            cd.name AS cattle_name,
            vm.id AS vaccine_id,
            vm.name,
            vm.booster_after_days,
            lv.last_vaccination,
            COALESCE(lv.next_date, CURRENT_DATE) AS next_date
        FROM cattle_data cd
        CROSS JOIN vaccine_master vm
        LEFT JOIN latest_vaccinations lv
            ON lv.tag_number = cd.tag_number
           AND lv.vaccine_id = vm.id
        WHERE cd.new_is_currently_present = 1
    ),

    vaccine_1_and_2 AS (
        SELECT
            tag_number,
            cattle_name,
            name,
            last_vaccination,
            next_date,
            CASE
                WHEN last_vaccination IS NULL THEN 'Pending'
                WHEN next_date < CURRENT_DATE THEN 'Overdue'
                WHEN next_date <= v_sixty_days_from_now THEN 'Pending'
                ELSE NULL
            END AS data
        FROM all_cattle_vaccines
        WHERE
            last_vaccination IS NULL
            OR next_date < CURRENT_DATE
            OR next_date <= v_sixty_days_from_now
    ),

    vaccine_3 AS (
        SELECT
            cd.tag_number,
            cd.name AS cattle_name,
            COALESCE(v.full_name, v.name) AS name,
            NULL::DATE AS last_vaccination,
            NULL::DATE AS next_date,
            'Pending'::TEXT AS data
        FROM cattle_data cd
        CROSS JOIN vaccine v
        WHERE
            cd.new_is_currently_present = 1
            AND v.id = 3
            AND LOWER(cd.gender) = 'female'
            AND (
                cd.brucellosis_status = 'NOT_VACCINATED'
                OR cd.brucellosis_status = 'UNKNOWN'
            )
    )

    SELECT json_agg(row_to_json(combined_data))
    INTO json_data
    FROM (
        SELECT
            tag_number,
            cattle_name,
            name,
            last_vaccination,
            next_date,
            data
        FROM vaccine_1_and_2
        WHERE data IS NOT NULL

        UNION ALL

        SELECT
            tag_number,
            cattle_name,
            name,
            last_vaccination,
            next_date,
            data
        FROM vaccine_3
    ) combined_data;

    RETURN COALESCE(json_data, '[]'::json);
END;
$$ LANGUAGE plpgsql;

select
    *
from
    cattle_vaccine ();


-- ============================================================
-- BRUCELLOSIS: only updates cattle_data.brucellosis_status
-- No insert into cattle_vaccine_logs
-- ============================================================
CREATE OR REPLACE FUNCTION vaccinate_brucellosis(
    p_tag_number TEXT
) RETURNS JSON AS $$
DECLARE
    v_result JSON;
BEGIN
    UPDATE cattle_data
    SET brucellosis_status = 'VACCINATED'
    WHERE tag_number = p_tag_number
      AND brucellosis_status = 'NOT_VACCINATED';

    GET DIAGNOSTICS v_result = ROW_COUNT;

    IF v_result = 0 THEN
        RETURN json_build_object('success', false, 'message', 'Cattle not found or already vaccinated');
    END IF;

    RETURN json_build_object(
        'success', true,
        'message', 'Brucellosis vaccination recorded',
        'tag_number', p_tag_number
    );
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- BATCH VACCINATION: insert into cattle_vaccine_logs
-- For vaccine_id=3 (Brucellosis), also updates cattle_data
-- ============================================================
CREATE OR REPLACE FUNCTION insert_cattle_vaccine_batch(
    p_tag_number TEXT,
    p_vaccine_id BIGINT,
    p_vaccinated_on DATE DEFAULT CURRENT_DATE
) RETURNS JSON AS $$
DECLARE
    v_id INTEGER;
    v_result JSON;
    v_updated BOOLEAN := false;
BEGIN
    INSERT INTO cattle_vaccine_logs (tag_number, vaccine_id, vaccinated_on)
    VALUES (p_tag_number, p_vaccine_id, p_vaccinated_on)
    RETURNING id INTO v_id;

    IF p_vaccine_id = 3 THEN
        UPDATE cattle_data
        SET brucellosis_status = 'VACCINATED'
        WHERE tag_number = p_tag_number;
        v_updated := true;
    END IF;

    v_result := json_build_object(
        'success', true,
        'message', 'Vaccination saved',
        'id', v_id,
        'tag_number', p_tag_number,
        'vaccine_id', p_vaccine_id,
        'vaccinated_on', p_vaccinated_on,
        'cattle_data_updated', v_updated
    );
    RETURN v_result;
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION insert_cattle_vaccine_batch_multi(
    p_records JSON
) RETURNS JSON AS $$
DECLARE
    v_record RECORD;
    v_results JSONB := '[]'::JSONB;
    v_success INTEGER := 0;
    v_failed INTEGER := 0;
    v_errors TEXT[] := '{}';
BEGIN
    FOR v_record IN
        SELECT * FROM json_populate_recordset(null::record, p_records)
        AS (tag_number TEXT, vaccine_id BIGINT, vaccinated_on DATE)
    LOOP
        BEGIN
            INSERT INTO cattle_vaccine_logs (tag_number, vaccine_id, vaccinated_on)
            VALUES (v_record.tag_number, v_record.vaccine_id,
                    COALESCE(v_record.vaccinated_on, CURRENT_DATE));

            IF v_record.vaccine_id = 3 THEN
                UPDATE cattle_data
                SET brucellosis_status = 'VACCINATED'
                WHERE tag_number = v_record.tag_number;
            END IF;

            v_success := v_success + 1;
        EXCEPTION WHEN OTHERS THEN
            v_failed := v_failed + 1;
            v_errors := array_append(v_errors,
                v_record.tag_number || ':' || SQLERRM);
        END;
    END LOOP;

    RETURN json_build_object(
        'success', true,
        'total', v_success + v_failed,
        'saved', v_success,
        'failed', v_failed,
        'errors', v_errors
    );
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- REGISTER NEW CATTLE
-- ============================================================
CREATE SEQUENCE IF NOT EXISTS cattle_data_id_seq START 405;

CREATE OR REPLACE FUNCTION register_cattle(
    p_name TEXT,
    p_tag_number TEXT DEFAULT NULL,
    p_acquisition_type TEXT DEFAULT NULL,
    p_date_of_birth TEXT DEFAULT NULL,
    p_animal_type TEXT DEFAULT NULL,
    p_mother_name TEXT DEFAULT NULL,
    p_mother_tag_number TEXT DEFAULT NULL,
    p_father_name TEXT DEFAULT NULL,
    p_father_tag_number TEXT DEFAULT NULL,
    p_new_is_currently_present BIGINT DEFAULT 1,
    p_new_is_currently_pregnant BIGINT DEFAULT 0,
    p_new_is_currenlty_milking BIGINT DEFAULT 0,
    p_weight_at_birth DOUBLE PRECISION DEFAULT NULL,
    p_gender TEXT DEFAULT NULL
) RETURNS JSON AS $$
DECLARE
    v_id BIGINT;
    v_tag TEXT;
BEGIN
    v_id := nextval('cattle_data_id_seq');
    v_tag := COALESCE(p_tag_number, 'SOM-' || LPAD(v_id::TEXT, 3, '0'));

    INSERT INTO cattle_data (
        id, name, tag_number, acquisition_type, date_of_birth,
        animal_type, mother_name, mother_tag_number,
        father_name, father_tag_number,
        new_is_currently_present, new_is_currently_pregnant,
        new_is_currenlty_milking, weight_at_birth, gender
    ) VALUES (
        v_id, p_name, v_tag, p_acquisition_type, p_date_of_birth,
        p_animal_type, p_mother_name, p_mother_tag_number,
        p_father_name, p_father_tag_number,
        p_new_is_currently_present, p_new_is_currently_pregnant,
        p_new_is_currenlty_milking, p_weight_at_birth, p_gender
    );

    RETURN json_build_object(
        'success', true,
        'message', 'Cattle registered successfully',
        'id', v_id,
        'tag_number', v_tag,
        'name', p_name
    );
END;
$$ LANGUAGE plpgsql;